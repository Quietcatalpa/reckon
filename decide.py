"""决策：规则先验 + Laya 判断，融合成“删除把握”，给出 建议删除 / 需要你看 / 建议保留。"""
import glob
import os
import threading
import time
import warnings

import config as C
import extract
from config import norm, under, under_any
from dupes import COPY_NAME

DELETE_AT = 0.70   # 把握 >= 0.70 建议删除
REVIEW_AT = 0.40   # 0.40 ~ 0.70 需要你看；更低建议保留
PERSONAL_CAP = 0.60  # 个人目录里的文档/代码最多到“需要你看”，不会被建议直接删除

# 实测（见 README）：noul 类问题对几乎所有文件都给出 0.9 以上，区分不开；
# 二选一的 choice 能把学习资料和软件包分开，所以删除决策只用这一问。
QUESTIONS = {
    "decision": {"type": "choice", "instructions": "用户在清理磁盘，这个文件应该删除还是保留？", "criteria": {
        "删除": "软件安装包、软件压缩包、临时文件、缓存、日志、重复下载的文件，删了可以重新下载",
        "保留": "学习资料、笔记、课件、教材、题库、论文、作业、申请书、计划书、个人作品和数据"}},
}
TOPICS = {
    "课程学习": "大学课程的课件、教材、笔记、习题、题库",
    "竞赛科研": "论文、数学建模、大创国创、实验报告",
    "求职升学": "简历、实习、考研保研、证书",
    "个人生活": "证件、账单、合同、照片",
    "软件工具": "软件、安装包、程序、驱动",
    "其他": "以上都不是",
}
TOPIC_Q = {"topic": {"type": "choice", "instructions": "根据文件名和内容，判断这个文件的用途", "criteria": TOPICS}}
TOPIC_MIN_P = 0.5  # Laya 主题分类不太准，只在没有关键词命中且把握 >= 0.5 时采用
# 主题优先按路径/文件名关键词判断（按顺序匹配第一个）
TOPIC_KEYWORDS = [
    ("求职升学", "简历 实习 求职 招聘 offer 考研 保研 推免 夏令营 升学 留学 雅思 托福 gre 证书 成绩单 面试 笔试"),
    ("竞赛科研", "科研 论文 数模 建模 竞赛 国赛 美赛 大创 国创 挑战杯 互联网+ 实验报告 paper thesis 文献 开题 课题"),
    ("课程学习", "课程 课件 教材 笔记 习题 题库 真题 期末 期中 作业 复习 讲义 教案 备课 lecture homework 学习资料"),
    ("个人生活", "身份证 护照 合同 发票 账单 租房 体检 户口 驾照 保险"),
    ("软件工具", "setup install 安装 客户端 client driver 驱动 portable 破解 插件 plugin"),
]


def _find_local_model():
    override = os.environ.get("LAYA_MODEL_DIR")
    if override and os.path.exists(os.path.join(override, "rl_agent_config.json")):
        return override
    for snap in glob.glob(os.path.join(C.LAYA_REPO, "snapshots", "*")):
        if os.path.exists(os.path.join(snap, "rl_agent_config.json")):
            return snap
    return None


class LayaJudge:
    """后台加载本地 Laya 多语言模型；找不到就只用规则。"""

    def __init__(self):
        self.agent = None
        self.status = "loading"  # loading / ready / missing / error
        self.error = None
        self.ready = threading.Event()
        self._lock = threading.Lock()

    def load(self):
        try:
            path = _find_local_model()
            if path is None:
                self.status = "missing"
                self.error = "没有找到本地的 convaiinnovations/laya-multilingual 模型"
                return
            os.environ["HF_HUB_OFFLINE"] = "1"
            warnings.filterwarnings("ignore")
            import laya
            self.agent = laya.load(path)
            self.status = "ready"
        except Exception as e:  # noqa: BLE001
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.ready.set()

    def ask(self, state, questions):
        with self._lock:
            return self.agent.predict(state, questions)["answers"]

    def judge(self, state, with_topic):
        qs = dict(QUESTIONS, **TOPIC_Q) if with_topic else QUESTIONS
        ans = self.ask(state, qs)
        out = {"delete": ans["decision"]["probabilities"]["删除"]}
        if with_topic:
            out["topic"] = ans["topic"]["choice"]
            out["topic_p"] = ans["topic"]["probabilities"][out["topic"]]
        return out


def keyword_topic(path):
    low = path.lower()
    for topic, words in TOPIC_KEYWORDS:
        if any(w in low for w in words.split()):
            return topic
    return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def _age_text(days):
    return "今天修改过" if days < 1 else f"{days} 天未修改"


def _rec(p):
    return "delete" if p >= DELETE_AT else "review" if p >= REVIEW_AT else "keep"


def rule_prior(path, np_, ftype, age, name_l):
    """返回 (规则把握, 理由, 是否跳过模型, 是否封顶在“需要你看”)。"""
    reasons = []
    in_appdata = under_any(np_, C.APPDATA_NS)
    personal = under_any(np_, C.PERSONAL_NS)
    in_chat = C.in_chat_dir(np_)
    in_downloads = under(np_, C.DOWNLOADS_N)
    ext = os.path.splitext(name_l)[1]
    if ext in C.PROGRAM_EXTS and ext != ".exe" and not in_downloads:
        return 0.2, ["程序组件文件，删除可能导致软件无法运行"], True, False
    if under(np_, C.TEMP_N):
        p = 0.9
        reasons.append("位于系统临时目录")
    elif in_downloads:
        p = 0.6
        reasons.append("位于下载文件夹")
    elif in_chat:
        p = 0.45
        reasons.append("聊天软件接收的文件")
    elif in_appdata:
        p = 0.35
        reasons.append("位于应用数据目录，可能是软件自己的数据")
    elif personal:
        p = 0.25
        reasons.append("位于个人资料目录")
    else:
        p = 0.35

    if ftype == "日志缓存":
        p = max(p, 0.85)
        reasons.append("临时/日志/残留下载类文件")
    elif ftype == "安装包":
        looks_installer = (under(np_, C.DOWNLOADS_N) or any(h in name_l for h in C.INSTALLER_NAME_HINTS)
                           or not name_l.endswith(".exe"))
        if looks_installer:
            p = 0.88 if age >= 30 else 0.5
            reasons.append(f"安装包，已放了 {age} 天" if age >= 30 else "近期下载的安装包")
        else:
            p = min(p, 0.3)
            reasons.append("可能是免安装程序本体")
    elif ftype in C.MEDIA_TYPES:
        p = 0.45 if age >= 365 else 0.2
        reasons.append("超过一年未修改的媒体文件" if age >= 365 else "媒体文件只按时间判断")
    elif ftype == "压缩包":
        stem = os.path.splitext(path)[0]
        if os.path.isdir(stem):
            p = max(p, 0.85)
            reasons.append("旁边已有解压出来的同名文件夹")

    if ftype not in C.MEDIA_TYPES and ftype != "安装包":
        if age > 365:
            p += 0.1
        elif age > 90:
            p += 0.05
        elif age < 14:
            p -= 0.1
    if COPY_NAME.search(name_l):
        p += 0.1
        reasons.append("文件名像是副本")
    if in_appdata:
        p = min(p, 0.5)
    # 上限：个人目录里的文档/代码/数据、聊天软件收到的文件（安装包和日志除外）最多到“需要你看”
    capped = (personal and ftype in ("文档", "代码", "数据")) or (in_chat and ftype not in ("安装包", "日志缓存"))
    return min(max(p, 0.02), 0.98), reasons, in_appdata, capped


def _laya_state(f, ftype, age, snippet):
    return {
        "文件名": os.path.basename(f["path"]),
        "所在文件夹": os.path.dirname(f["path"]),
        "类型": ftype,
        "大小": human_size(f["size"]),
        "最后修改": _age_text(age),
        "内容片段": snippet or "（未读取内容）",
    }


def build_items(scan, dup_groups, judge, opts, on_progress, stop):
    now = time.time()
    min_bytes = int(opts.get("min_size_mb", 20) * (1 << 20))
    max_laya = int(opts.get("max_laya", 300))
    items = []

    def add(item):
        item["id"] = len(items)
        items.append(item)
        return item

    # 1) 缓存目录组：纯规则
    for g in sorted(scan.groups.values(), key=lambda g: -g["size"]):
        if g["size"] < (1 << 20):
            continue
        age = int((now - g["newest"]) / 86400) if g["newest"] else 0
        add({"kind": "group", "name": g["label"], "path": g["paths"][0], "paths": g["paths"],
             "size": g["size"], "count": g["count"], "mtime": g["newest"], "age": age,
             "type": "缓存目录", "topic": None, "p": g["p"], "rec": _rec(g["p"]),
             "reasons": [g["hint"], f"{g['count']:,} 个文件", _age_text(age)], "laya": None})

    # 2) 重复文件：保留一份，其余建议删除
    dupe_paths = set()
    for dg in dup_groups:
        for cp in dg["copies"]:
            dupe_paths.add(cp)
    by_path = {f["path"]: f for f in scan.files}
    dup_out = []
    for gi, dg in enumerate(sorted(dup_groups, key=lambda d: -d["size"] * len(d["copies"]))):
        ids = []
        for cp in dg["copies"]:
            f = by_path.get(cp)
            if f is None:
                continue
            age = int((now - f["mtime"]) / 86400)
            p = 0.88 if dg["exact"] else 0.78
            it = add({"kind": "dupe", "name": os.path.basename(cp), "path": cp, "size": f["size"],
                      "mtime": f["mtime"], "age": age, "type": C.file_type(os.path.splitext(cp)[1].lower()),
                      "topic": keyword_topic(cp), "p": p, "rec": _rec(p), "dup_of": dg["keep"], "dup_group": gi,
                      "reasons": ["与保留的那份内容完全相同" if dg["exact"] else "与保留的那份抽样比对一致（超大文件）"],
                      "laya": None})
            ids.append(it["id"])
        if ids:
            dup_out.append({"keep": dg["keep"], "size": dg["size"], "exact": dg["exact"], "ids": ids})

    # 3) 单个文件：大文件 + 下载文件夹里的文件
    cands = []
    for f in scan.files:
        if f["path"] in dupe_paths:
            continue
        np_ = norm(f["path"])
        if f["size"] >= min_bytes or under(np_, C.DOWNLOADS_N):
            cands.append((f, np_))
    cands.sort(key=lambda x: -x[0]["size"])

    use_laya = judge is not None and judge.status == "ready"
    laya_budget = max_laya if use_laya else 0
    pending = []
    for f, np_ in cands:
        name = os.path.basename(f["path"])
        ext = os.path.splitext(name)[1].lower()
        ftype = C.file_type(ext)
        age = max(0, int((now - f["mtime"]) / 86400))
        p, reasons, skip_model, capped = rule_prior(f["path"], np_, ftype, age, name.lower())
        reasons.append(_age_text(age))
        if capped:
            p = min(p, PERSONAL_CAP)
        item = add({"kind": "file", "name": name, "path": f["path"], "size": f["size"], "mtime": f["mtime"],
                    "age": age, "type": ftype, "topic": keyword_topic(f["path"]), "p": p, "rec": _rec(p), "reasons": reasons,
                    "laya": None, "_rule": p, "_capped": capped, "_ext": ext, "_cloud": f["cloud"]})
        if ftype not in C.NO_MODEL_TYPES and not skip_model:
            pending.append(item)

    n_model = min(len(pending), laya_budget)
    t0 = time.time()
    for i, item in enumerate(pending):
        if stop.is_set():
            break
        if i >= laya_budget:
            if use_laya:
                item["reasons"].append("超出模型判断数量上限，仅按规则")
            continue
        snip = None if item["_cloud"] else extract.snippet(item["path"], item["_ext"])
        state = _laya_state(item, item["type"], item["age"], snip)
        try:
            j = judge.judge(state, with_topic=item["topic"] is None and item["type"] in ("文档", "数据", "压缩包"))
        except Exception as e:  # noqa: BLE001
            item["reasons"].append(f"模型判断失败：{type(e).__name__}")
            continue
        p = 0.45 * item["_rule"] + 0.55 * j["delete"]
        if item["_capped"]:
            p = min(p, PERSONAL_CAP)
        item["p"] = round(p, 3)
        item["rec"] = _rec(p)
        # 文档被分到“软件工具”基本都是误判（实测 PPT 常被这样分），不采用
        if (item["topic"] is None and j.get("topic_p", 0) >= TOPIC_MIN_P
                and not (item["type"] == "文档" and j["topic"] == "软件工具")):
            item["topic"] = j["topic"]
        item["laya"] = {"delete": round(j["delete"], 3), "read": bool(snip),
                        "topic": j.get("topic"), "topic_p": round(j.get("topic_p", 0), 3)}
        item["reasons"].append(f"Laya：倾向删除 {j['delete']:.0%}"
                               + ("（已读内容）" if snip else "（仅看文件信息）"))
        if i % 3 == 0:
            el = time.time() - t0
            eta = el / (i + 1) * (n_model - i - 1)
            on_progress(f"Laya 判断中 {i + 1}/{n_model} · 预计还需 {int(eta // 60)} 分 {int(eta % 60)} 秒",
                        (i + 1) / max(n_model, 1))

    for it in items:
        for k in [k for k in it if k.startswith("_")]:
            del it[k]
        it["p"] = round(it["p"], 3)
    return items, dup_out
