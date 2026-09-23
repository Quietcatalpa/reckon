"""用 cases.jsonl 里手工标注的虚构样例评估清理建议，比较不同设置。

    python docs/eval/run_eval.py            # 规则 + Laya（需要本地模型）
    python docs/eval/run_eval.py --no-laya  # 只评估规则

每条样例只有文件信息（名字、位置、大小、多久没改、内容片段）和人工认为“应该”落在哪一栏：
delete 建议删除 / review 需要你看 / keep 建议保留。样例是编的，不对应任何真实电脑；
标注带主观性，样本也不大，结果只用来比较设置、发现明显的错判，不能当作准确率宣传。

最重要的指标是“危险错误”：应该保留的被放进了“建议删除”。其次是“该看的被建议删除”。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from reckon import config as C  # noqa: E402
from reckon import decide  # noqa: E402

PLACES = {
    "downloads": C.DOWNLOADS,
    "desktop": C.DESKTOP,
    "documents": C.DOCUMENTS,
    "chat": os.path.join(C.DOCUMENTS, "WeChat Files", "wxid_demo", "FileStorage", "File", "2025-01"),
    "appdata": os.path.join(C.LOCALAPPDATA, "SomeApp"),
    "other": r"D:\data\misc",
}
LABELS = ("delete", "review", "keep")
NAMES = {"delete": "建议删除", "review": "需要你看", "keep": "建议保留"}


def load_cases():
    with open(os.path.join(HERE, "cases.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def prepare(case):
    """算出规则部分，和扫描时走的是同一段代码。"""
    path = os.path.join(PLACES[case["where"]], case["name"])
    ext = os.path.splitext(case["name"])[1].lower()
    ftype = C.file_type(ext)
    rule_p, reasons, skip_model, capped = decide.rule_prior(path, C.norm(path), ftype, case["age"], case["name"].lower())
    if capped:
        rule_p = min(rule_p, decide.PERSONAL_CAP)
    state = {"path": path, "size": int(case["size_mb"] * (1 << 20))}
    use_model = ftype not in C.NO_MODEL_TYPES and not skip_model
    return {"path": path, "ftype": ftype, "rule_p": rule_p, "capped": capped, "use_model": use_model, "state": state}


def score(cases, preps, laya_out, w_rule, review_at=None):
    old, old_r = decide.W_RULE, decide.REVIEW_AT
    decide.W_RULE = w_rule
    if review_at is not None:
        decide.REVIEW_AT = review_at
    try:
        preds = []
        for c, pr, ld in zip(cases, preps, laya_out):
            p = decide.fuse(pr["rule_p"], ld if pr["use_model"] else None, pr["capped"])
            preds.append(decide._rec(p))
    finally:
        decide.W_RULE, decide.REVIEW_AT = old, old_r
    conf = {(a, b): 0 for a in LABELS for b in LABELS}
    for c, pred in zip(cases, preds):
        conf[(c["label"], pred)] += 1
    right = sum(conf[(a, a)] for a in LABELS)
    return preds, conf, {
        "准确": f"{right}/{len(cases)}",
        "危险(该留→删)": conf[("keep", "delete")],
        "该看→删": conf[("review", "delete")],
        "该删→留": conf[("delete", "keep")],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-laya", action="store_true")
    ap.add_argument("--chars", type=int, nargs="*", default=[decide.SNIPPET_CHARS], help="内容片段长度，可以给多个比较")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases = load_cases()
    preps = [prepare(c) for c in cases]
    print(f"{len(cases)} 条样例：" + "，".join(f"{NAMES[k]} {sum(c['label'] == k for c in cases)}" for k in LABELS))

    rows = [("只用规则", score(cases, preps, [None] * len(cases), decide.W_RULE))]
    if not args.no_laya:
        judge = decide.LayaJudge()
        judge.load()
        if judge.status != "ready":
            sys.exit(f"Laya 没加载成功：{judge.error}")
        for n in args.chars:
            outs = []
            for c, pr in zip(cases, preps):
                if not pr["use_model"]:
                    outs.append(None)
                    continue
                st = decide._laya_state(pr["state"], pr["ftype"], c["age"], (c.get("snippet") or "")[:n] or None)
                outs.append(judge.judge(st, with_topic=False)["delete"])
            for w in (0.3, 0.45, 0.6):
                rows.append((f"规则 {w:.2f} + Laya {1 - w:.2f}（片段 {n} 字）", score(cases, preps, outs, w)))
            for ra in (0.3, 0.35):
                rows.append((f"  同上 0.45，需要你看下限 {ra:.2f}", score(cases, preps, outs, decide.W_RULE, ra)))
    print()
    for label, (_, _, m) in rows:
        print(f"{label:28}  " + "  ".join(f"{k} {v}" for k, v in m.items()))

    # 当前默认设置的错判明细
    label, (preds, conf, _) = next((r for r in rows if f"{decide.W_RULE:.2f} +" in r[0]), rows[0])
    print(f"\n当前设置（{label}）的混淆矩阵，行 = 标注，列 = 预测：")
    print("            " + "  ".join(f"{NAMES[b]:>6}" for b in LABELS))
    for a in LABELS:
        print(f"{NAMES[a]:6}  " + "  ".join(f"{conf[(a, b)]:>8}" for b in LABELS))
    print("\n判错的样例：")
    for c, pred in zip(cases, preds):
        if pred != c["label"]:
            print(f"  标注 {NAMES[c['label']]} → 预测 {NAMES[pred]}  {c['name']}（{c['where']}）")


if __name__ == "__main__":
    main()
