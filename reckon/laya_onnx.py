"""不依赖 torch 的 Laya 推理：tokenizers 分词 + onnxruntime 跑 scripts/export_onnx.py 导出的模型。

exe 版用这个（体积小很多）；从源码运行、装了 torch 和 laya 时仍然可以用原版。
接口和 laya.Agent 的 predict(state, questions) 一样，只支持 choice 类问题（本项目只用到它）。

输入序列的拼法（render_options、build_sequence）移植自 laya 0.3.6 的 laya/common.py，
原作者 Convai Innovations，Apache License 2.0，见 THIRD_PARTY_NOTICES.md。
"""
import json
import os

import numpy as np

CHOICE = 0  # laya 里 choice 问题的类型编号


def _render_criterion(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)


def _render_options(crit):
    """choice 问题的选项文字，顺序就是标签顺序。"""
    return [k if v is None or v == "" else "%s: %s" % (k, _render_criterion(v)) for k, v in crit.items()]


def _fp16_storage_to_fp32(data):
    """发布的模型文件把权重矩阵按半精度存（体积减半），加载时换回 fp32 再算，结果和 fp32 几乎一样。
    （直接 int8 量化会让约 20% 的文件换栏，不能用。）"""
    import onnx
    from onnx import numpy_helper
    m = onnx.load_from_string(data)
    for init in m.graph.initializer:
        if init.data_type == onnx.TensorProto.FLOAT16:
            init.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(init).astype(np.float32), init.name))
    return m.SerializeToString()


class OnnxAgent:
    def __init__(self, model_dir, model_file=None, threads=None):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        with open(os.path.join(model_dir, "laya_meta.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        path = os.path.join(model_dir, model_file or self.meta.get("model_file", "model.onnx"))
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        # onnxruntime 在 Windows 上也处理不了带中文的路径，所以把模型读成字节再交给它
        with open(path, "rb") as f:
            data = f.read()
        if path.endswith(".fp16.onnx"):
            data = _fp16_storage_to_fp32(data)
        self.sess = ort.InferenceSession(data, opts, providers=["CPUExecutionProvider"])
        self.model_id = self.meta.get("source_snapshot", "onnx") + ":" + os.path.basename(path)

    def _ids(self, text):
        return self.tok.encode(text, add_special_tokens=False).ids

    def build_sequence(self, state, instructions, crit):
        """格式：[CLS] <类型> instructions [SEP] [MASK] 选项0 [MASK] 选项1 ... [SEP] state [SEP]。"""
        m = self.meta
        mask_tok, max_len, head_max = m["mask_token"], m["max_len"], m["head_max_len"]
        opts = _render_options(crit)
        head_ids = self._ids("choice question: %s" % str(instructions).replace(mask_tok, " "))
        opt_ids = [[m["mask_token_id"]] + self._ids(" " + o.replace(mask_tok, " "))[:48] for o in opts]
        budget = head_max - sum(len(o) for o in opt_ids)
        if budget < 16:
            per = max(4, (head_max - 16) // max(1, len(opt_ids)))
            opt_ids = [o[:per] for o in opt_ids]
            budget = head_max - sum(len(o) for o in opt_ids)
        head_ids = head_ids[: max(8, budget)]
        ids = [m["cls_token_id"]] + head_ids + [m["sep_token_id"]]
        markers = []
        for o in opt_ids:
            markers.append(len(ids))
            ids.extend(o)
        ids.append(m["sep_token_id"])
        room = max(0, max_len - len(ids) - 1)
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        ids = ids + self._ids(text.replace(mask_tok, " "))[:room] + [m["sep_token_id"]]
        return ids[:max_len], [x for x in markers if x < max_len]

    def _temperature(self, k):
        size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
        t = self.meta.get("temperature_by_options", {}).get("choice:" + size, self.meta["temperature"][CHOICE])
        try:
            t = float(t)
        except (TypeError, ValueError):
            return 1.0
        return min(5.0, max(0.5, t)) if t == t else 1.0

    def predict(self, state, questions):
        rows = []
        for qid, qd in questions.items():
            if qd["type"] != "choice":
                raise ValueError("OnnxAgent 只支持 choice 问题")
            crit = qd["criteria"]
            if isinstance(crit, list):
                crit = {c: None for c in crit}
            ins = qd["instructions"] if isinstance(qd["instructions"], str) else json.dumps(qd["instructions"])
            ids, markers = self.build_sequence(state, ins, crit)
            if len(markers) != len(crit):
                raise ValueError("question %r options exceed head_max_len" % qid)
            rows.append((qid, list(crit), ids, markers))
        n, L = len(rows), max(len(r[2]) for r in rows)
        kmax = max(len(r[3]) for r in rows)
        input_ids = np.full((n, L), self.meta["pad_token_id"], dtype=np.int64)
        att = np.zeros((n, L), dtype=np.int64)
        mpos = np.zeros((n, kmax), dtype=np.int64)
        mmask = np.zeros((n, kmax), dtype=bool)
        for i, (_, _, ids, markers) in enumerate(rows):
            input_ids[i, : len(ids)] = ids
            att[i, : len(ids)] = 1
            mpos[i, : len(markers)] = markers
            mmask[i, : len(markers)] = True
        logits = self.sess.run(None, {"input_ids": input_ids, "attention_mask": att, "marker_pos": mpos,
                                      "marker_mask": mmask, "qtype": np.zeros(n, dtype=np.int64)})[0]
        answers = {}
        for i, (qid, keys, _, _) in enumerate(rows):
            k = len(keys)
            z = logits[i, :k].astype(np.float64) / self._temperature(k)
            p = np.exp(z - z.max())
            p /= p.sum()
            best = int(p.argmax())
            answers[qid] = {"type": "choice", "choice": keys[best],
                            "probabilities": {kk: round(float(v), 4) for kk, v in zip(keys, p)}}
        return {"model": "laya-onnx", "answers": answers, "usage": {"input_tokens": int(att.sum()), "output_tokens": 0}}
