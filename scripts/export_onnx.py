"""把本地的 Laya 多语言模型导出成 ONNX，给 exe 版用（exe 里不带 torch，用 onnxruntime 推理）。

    python scripts/export_onnx.py            # 输出到 build/laya-onnx/

需要：laya、torch、transformers、onnx、onnxruntime（只有导出时需要，运行 exe 不需要）。
输出目录里有：
  model.onnx        fp32，和 torch 原版逐位一致（源码运行时用）
  model.fp16.onnx   权重矩阵按半精度存储，体积减半；加载时换回 fp32 计算，结果和原版一致（发布的 exe 用）
                    int8 量化试过：约 20% 的文件会换栏，不能用。
  tokenizer.json    分词器
  laya_meta.json    推理需要的参数（序列长度、温度等）
Laya 模型权重按 Apache-2.0 发布，分发时需要带上它的许可证（见 THIRD_PARTY_NOTICES.md）。
"""
import glob
import json
import os
import shutil
import sys
import time
import warnings

warnings.filterwarnings("ignore")
os.environ["HF_HUB_OFFLINE"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "build", "laya-onnx")
# onnx 的 C++ 部分在 Windows 上处理不了带中文的路径，先在纯英文的临时目录里做，最后再拷回来
WORK = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "reckon_onnx_build")

import numpy as np  # noqa: E402
import torch  # noqa: E402

from reckon import config as C  # noqa: E402
from reckon import decide  # noqa: E402


def _self_attn(attn, x, pad):
    """和 nn.MultiheadAttention 一样的计算，但形状都用动态的写法。
    nn.MultiheadAttention 导出时会把序列长度写死成常数，换个长度的输入就报错。"""
    b, _, d = x.shape
    h = attn.num_heads
    q, k, v = torch.nn.functional.linear(x, attn.in_proj_weight, attn.in_proj_bias).chunk(3, dim=-1)
    q, k, v = (t.reshape(b, -1, h, d // h).transpose(1, 2) for t in (q, k, v))
    mask = torch.zeros(pad.shape, dtype=q.dtype).masked_fill(pad, float("-inf"))[:, None, None, :]
    out = torch.nn.functional.scaled_dot_product_attention(q, k, v, attn_mask=mask)
    return attn.out_proj(out.transpose(1, 2).reshape(b, -1, d))


class LogitsOnly(torch.nn.Module):
    """复刻 laya DecisionModel.forward 里算打分的部分（不要 act 头），输出每个选项位置的 logit。"""

    def __init__(self, model):
        super().__init__()
        self.m = model

    def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
        m = self.m
        h = m.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        h = h + m.type_emb(qtype)[:, None, :]
        pad = ~attention_mask.bool()
        for layer in m.head.layers:  # TransformerEncoderLayer，norm_first=True
            h = h + _self_attn(layer.self_attn, layer.norm1(h), pad)
            h = h + layer.linear2(layer.activation(layer.linear1(layer.norm2(h))))
        idx = marker_pos.clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
        logits = m.scorer(torch.gather(h, 1, idx)).squeeze(-1).float()
        return logits.masked_fill(~marker_mask, -1e4)


def sample_batch(agent, states, questions):
    from laya.common import QTYPES, build_sequence, collate_items
    rows = []
    for st in states:
        group = []
        for qd in questions.values():
            q = agent._to_internal(qd)
            seq, markers = build_sequence(agent.tok, st, q, agent.cfg.get("max_len", 512), agent.cfg.get("head_max_len", 192))
            group.append({"ids": seq, "markers": markers, "qtype": QTYPES[q["t"]]})
        rows.append(group)
    b = collate_items(rows, agent.tok.pad_token_id)
    return tuple(b[k] for k in ("input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype"))


def run_onnx(path, batch):
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    names = [i.name for i in sess.get_inputs()]
    feed = {n: t.numpy() for n, t in zip(names, batch)}
    feed["marker_mask"] = feed["marker_mask"].astype(bool)
    return sess.run(None, feed)[0]


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    snap = next(s for s in glob.glob(os.path.join(C.LAYA_REPO, "snapshots", "*"))
                if os.path.exists(os.path.join(s, "rl_agent_config.json")))
    import laya
    agent = laya.load(snap)
    agent.model.eval()
    torch.backends.mha.set_fastpath_enabled(False)  # nn.TransformerEncoderLayer 的快速路径没法导出
    wrapper = LogitsOnly(agent.model).eval()
    check = sample_batch(agent, [{"文件名": "x.pdf", "内容片段": "内容 " * 50}, {"文件名": "y.zip"}], decide.QUESTIONS)
    with torch.no_grad():
        diff = (wrapper(*check) - agent.model(*check)[0]).abs()[check[3]].max().item()
    print(f"手写注意力层和原模型的差：{diff:.2e}")
    assert diff < 1e-3

    states = [{"文件名": "setup.exe", "大小": "12 MB", "内容片段": "安装程序"},
              {"文件名": "期末复习笔记.pdf", "所在文件夹": r"D:\Downloads", "内容片段": "第三章 多元回归 " * 30}]
    qs = dict(decide.QUESTIONS, **decide.TOPIC_Q)
    batch = sample_batch(agent, states, qs)
    fp32 = os.path.join(WORK, "model.onnx")
    t = time.time()
    torch.onnx.export(
        wrapper, batch, fp32, dynamo=False, opset_version=17,
        input_names=["input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "n", 1: "L"}, "attention_mask": {0: "n", 1: "L"},
                      "marker_pos": {0: "n", 1: "k"}, "marker_mask": {0: "n", 1: "k"}, "qtype": {0: "n"},
                      "logits": {0: "n", 1: "k"}})
    print(f"导出 fp32 完成 {time.time() - t:.0f}s，{os.path.getsize(fp32) / 1e6:.0f} MB")

    import onnx
    from onnx import numpy_helper
    m = onnx.load(fp32)
    for init in m.graph.initializer:  # 只把权重矩阵存成半精度，偏置、LayerNorm 等小参数保持 fp32
        if init.data_type == onnx.TensorProto.FLOAT and len(init.dims) >= 2:
            init.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(init).astype(np.float16), init.name))
    fp16 = os.path.join(WORK, "model.fp16.onnx")
    with open(fp16, "wb") as f:
        f.write(m.SerializeToString())
    print(f"半精度存储版完成，{os.path.getsize(fp16) / 1e6:.0f} MB")

    shutil.copy(os.path.join(snap, "tokenizer", "tokenizer.json"), os.path.join(OUT, "tokenizer.json"))
    tok = agent.tok
    meta = {"max_len": agent.cfg.get("max_len", 512), "head_max_len": agent.cfg.get("head_max_len", 192),
            "temperature": agent.temperature, "temperature_by_options": agent.temperature_by_options,
            "pad_token_id": tok.pad_token_id, "cls_token_id": tok.cls_token_id, "sep_token_id": tok.sep_token_id,
            "mask_token_id": tok.mask_token_id, "mask_token": tok.mask_token, "source_snapshot": os.path.basename(snap),
            "model_file": "model.fp16.onnx"}
    with open(os.path.join(OUT, "laya_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    # 核对：换一批没见过的输入，比较 torch / onnx fp32 / 半精度存储版
    test = sample_batch(agent, [{"文件名": "年度总结.pptx", "内容片段": "一、主要成绩 二、存在问题"},
                                {"文件名": "v2ray.zip", "内容片段": "压缩包内共 25 个文件：v2ray.exe；config.json"},
                                {"文件名": "a" * 5, "内容片段": "测试 " * 400}], qs)
    with torch.no_grad():
        ref = wrapper(*test).float().numpy()
    mask = test[3].numpy()
    for fn in ("model.onnx", "model.fp16.onnx"):
        shutil.move(os.path.join(WORK, fn), os.path.join(OUT, fn))
    sys.path.insert(0, ROOT)
    from reckon.laya_onnx import OnnxAgent
    for fn in ("model.onnx", "model.fp16.onnx"):
        ag = OnnxAgent(OUT, fn)
        out = ag.sess.run(None, {"input_ids": test[0].numpy(), "attention_mask": test[1].numpy(), "marker_pos": test[2].numpy(),
                                 "marker_mask": test[3].numpy().astype(bool), "qtype": test[4].numpy()})[0]
        print(f"{fn}: 和 torch 的最大 logit 差 {np.abs(out - ref)[mask].max():.4f}")
    shutil.rmtree(WORK, ignore_errors=True)
    print("已输出到", OUT)


if __name__ == "__main__":
    main()
