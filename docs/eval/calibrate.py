"""给 Laya“删除 / 保留”这道题拟合温度（校准），用 cases.jsonl 里的标注样例。

    python docs/eval/calibrate.py

laya-multilingual 出厂没有拟合温度（官方说明：普遍过度自信），需要自己用标注数据拟合。
只有两个选项时，温度作用在概率上：logit(p') = logit(p) / T，T > 1 让概率往 0.5 收（更谨慎）。

只用标注为“建议删除”和“建议保留”的样例拟合（“需要你看”本身就是拿不准，不当作标准答案），
在 [1, 5] 里网格搜索使负对数似然最小的 T；不往 T < 1 调，因为问题是过度自信，不该变得更自信。
另外用留一法看 T 稳不稳定，并报告校准误差（ECE）的变化。样本少，结果只作参考。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from reckon import decide  # noqa: E402
from run_eval import load_cases, prepare  # noqa: E402

GRID = np.round(np.arange(1.0, 5.0001, .05), 2)


def scale(p, t):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return 1 / (1 + np.exp(-np.log(p / (1 - p)) / t))


def nll(p, y, t):
    q = scale(p, t)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def fit(p, y):
    return float(GRID[int(np.argmin([nll(p, y, t) for t in GRID]))])


def ece(p, y, bins=5):
    """把预测“删除”的把握分箱，比较平均把握和实际比例。"""
    conf = np.maximum(p, 1 - p); pred = (p >= .5).astype(int); right = (pred == y).astype(float)
    edges = np.linspace(.5, 1, bins + 1); e = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        m = (conf >= a) & (conf < b if b < 1 else conf <= b)
        if m.any():
            e += m.mean() * abs(conf[m].mean() - right[m].mean())
    return e


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases = load_cases()
    judge = decide.LayaJudge(); judge.load()
    if judge.status != "ready":
        sys.exit(f"Laya 没加载成功：{judge.error}")
    ps, ys = [], []
    for c in cases:
        pr = prepare(c)
        if not pr["use_model"] or c["label"] == "review":
            continue
        st = decide._laya_state(pr["state"], pr["ftype"], c["age"], (c.get("snippet") or "")[:decide.SNIPPET_CHARS] or None)
        ps.append(judge.judge(st, with_topic=False)["delete"]); ys.append(1 if c["label"] == "delete" else 0)
    p, y = np.array(ps), np.array(ys)
    print(f"用于拟合的样例 {len(p)} 条（建议删除 {int(y.sum())}，建议保留 {int(len(y) - y.sum())}），模型后端：{judge.backend}")
    t = fit(p, y)
    loo = [fit(np.delete(p, i), np.delete(y, i)) for i in range(len(p))]
    print(f"\n拟合出的温度 T = {t}（留一法范围 {min(loo)}–{max(loo)}，中位数 {np.median(loo)}）")
    acc = lambda q: float(((q >= .5).astype(int) == y).mean())
    print(f"负对数似然：{nll(p, y, 1.0):.3f} → {nll(p, y, t):.3f}")
    print(f"校准误差 ECE：{ece(p, y):.3f} → {ece(scale(p, t), y):.3f}")
    print(f"平均把握：{np.maximum(p, 1 - p).mean():.3f} → {np.maximum(scale(p, t), 1 - scale(p, t)).mean():.3f}，"
          f"二选一准确率 {acc(p):.3f}（温度不改变谁大谁小，所以准确率不变）")


if __name__ == "__main__":
    main()
