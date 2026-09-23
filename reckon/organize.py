"""文件整理：给交进来的文件/文件夹找去处，确认后移动，并记录下来以便撤销。

两种方式：
- topic：按主题放进“目标文件夹”的现有分类。依次尝试：文件名里出现了某个已有文件夹的名字 →
  关键词判断主题 → Laya 在顶层分类里选一个。都不行就标成“需要你选”。
- type：按文件类型分到 文档/图片/安装包… 子文件夹。

交进来的是文件夹时，只整理它的第一层内容：散落的文件逐个处理，子文件夹整体移动（不拆开）。
"""
import json
import os
import re
import shutil
import time

from . import config as C
from . import extract
from . import scanner
from .config import norm, under

HISTORY_DIR = C.HISTORY_DIR
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
SKIP_EXTS = {".lnk", ".url"}
HIDDEN_OR_SYSTEM = 0x2 | 0x4
TYPE_DIRS = {"日志缓存": "临时与日志"}  # 其他类型直接用类型名作文件夹名
# 太笼统的文件夹名，不拿来做文件名匹配（否则“数学作业”会被塞进随便哪门课的“作业”文件夹）
STOP_WORDS = set("""
新建文件夹 资料 文件 文档 图片 视频 照片 素材 模板 其他 其它 杂项 备份 附件 下载 最终版 论文 报告 代码 作业 笔记
课件 数据 汇总 未命名 临时 输出 结果 截图 音频 音乐 项目 工具 软件 学习 课程资料 学习资料
data images image img imgs pics docs doc src code output outputs test tests temp tmp backup files file misc
other others download downloads assets resources new
""".split())
_NUM_PREFIX = re.compile(r"^\s*\d+[\s_\-.、)）]*")
# 关键词分组：(组名, 关键词)。组名用来和目标文件夹的分类名对上（按两字词重叠），
# 所以组名里放几个常见叫法，比如“个人生活”能对上“03_生活账单”。
KEYWORD_GROUPS = [
    ("升学求职实习", "简历 实习 求职 招聘 offer 考研 保研 推免 夏令营 升学 留学 面试 笔试 成绩单 推荐信"),
    ("竞赛比赛", "竞赛 比赛 国赛 美赛 数模 建模 挑战杯 互联网+ 大创 国创 创新创业 蓝桥杯 获奖"),
    ("科研论文", "科研 论文 文献 开题 课题 实验报告 paper thesis 综述 投稿 期刊"),
    ("英语外语", "英语 雅思 托福 四级 六级 cet ielts toefl gre 单词 口语 听力"),
    ("课程学习", "课程 课件 教材 习题 题库 真题 期末 期中 作业 复习 讲义 lecture homework 笔记"),
    ("学生事务", "奖学金 助学金 评优 综测 学生会 团委 党员 入党 志愿 请假 学工 班级"),
    ("素材模板工具", "模板 素材 ppt模板 字体 图标 setup install 安装 客户端 驱动 插件 portable 工具"),
    ("个人生活", "身份证 护照 合同 发票 账单 租房 体检 户口 驾照 保险 照片"),
]


def clean_name(name):
    """'04_旅行' -> '旅行'。"""
    return _NUM_PREFIX.sub("", name).strip() or name


def _usable(n):
    if not n or n.lower() in STOP_WORDS:
        return False
    cjk = sum(1 for ch in n if "一" <= ch <= "鿿")
    if cjk:
        return cjk >= 2
    return len(n) >= 4 and not n.isdigit()


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)}


def build_index(root, max_depth=3, limit=3000):
    """目标文件夹下的目录索引（最多 3 层），跳过隐藏目录和软件目录。"""
    out = []
    stack = [(root, 0)]
    while stack and len(out) < limit:
        d, depth = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        names = {e.name.lower() for e in entries}
        if depth > 0 and (names & C.UNINSTALL_MARKERS or scanner._looks_like_program(d, names)):
            continue
        for e in entries:
            n = e.name
            if scanner._is_link(e) or not e.is_dir(follow_symlinks=False):
                continue
            if n.startswith(".") or n.lower() in C.SKIP_DIR_NAMES or n.lower() in ("node_modules", "__pycache__"):
                continue
            rel = os.path.relpath(e.path, root)
            out.append({"path": e.path, "rel": rel, "name": n, "clean": clean_name(n),
                        "depth": depth + 1, "top": rel.split(os.sep)[0]})
            if depth + 1 < max_depth:
                stack.append((e.path, depth + 1))
    return out


def program_dir_of(path):
    """path 本身或它的某一层上级是软件安装目录 / 解压的软件 / conda 环境时，返回那个目录。

    软件目录里的 exe、dll、配置文件被挪走，软件就打不开了，所以整理时整棵树都不碰。
    判断规则和扫描时跳过软件目录用的是同一套（卸载程序、exe + 多个 dll、Electron 应用、conda-meta）。
    """
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while True:
        try:
            names = {n.lower() for n in os.listdir(d)}
        except OSError:
            names = set()
        if names & C.UNINSTALL_MARKERS or "conda-meta" in names or scanner._looks_like_program(d, names):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _forbidden_source(np_):
    parts = np_.rstrip("\\").split("\\")
    return len(parts) == 1 or np_ == norm(C.HOME) or any(under(np_, b) for b in C.APPDATA_NS)


def collect_units(sources, target_root):
    """把交进来的路径展开成待整理的单元，返回 (units, 跳过的说明)。"""
    units, notes, seen = [], [], set()
    tr_n = norm(target_root) if target_root else None

    def add(path, is_dir):
        np_ = norm(path)
        name = os.path.basename(path)
        if np_ in seen or name.lower() in SKIP_NAMES or name.startswith("."):
            return
        if not is_dir and os.path.splitext(name)[1].lower() in SKIP_EXTS:
            return
        if C.is_protected(np_):
            return
        try:
            st = os.stat(path)
        except OSError:
            return
        if getattr(st, "st_file_attributes", 0) & HIDDEN_OR_SYSTEM:
            return
        if tr_n and (under(tr_n, np_)):
            return  # 目标文件夹本身或它的上级
        if is_dir:
            try:
                kids = [e.name for e in os.scandir(path)]
            except OSError:
                return
            low = {k.lower() for k in kids}
            if low & C.UNINSTALL_MARKERS or "conda-meta" in low:
                notes.append(f"跳过已安装的软件目录：{path}")
                return
            size = scanner.dir_stats(path)[0]
        else:
            kids = []
            size = st.st_size
        seen.add(np_)
        units.append({"path": path, "name": name, "is_dir": is_dir, "size": size, "mtime": st.st_mtime,
                      "kids": kids[:40], "n_kids": len(kids)})

    for src in sources:
        src = os.path.abspath(src.strip().strip('"'))
        np_ = norm(src)
        if not os.path.exists(src):
            notes.append(f"找不到：{src}")
            continue
        if C.is_protected(np_) or _forbidden_source(np_):
            notes.append(f"为安全起见不整理这个位置：{src}")
            continue
        prog = program_dir_of(src)
        if prog:
            where = "本身就是软件目录" if norm(prog) == np_ else f"在软件目录「{prog}」里面"
            notes.append(f"「{src}」{where}，移动里面的文件会让软件打不开，已跳过")
            continue
        if os.path.isdir(src):
            # 整理目标文件夹自身时，它的第一层子文件夹就是分类，不动
            skip_dirs = tr_n is not None and np_ == tr_n
            try:
                entries = list(os.scandir(src))
            except OSError:
                notes.append(f"没有权限读取：{src}")
                continue
            for e in entries:
                if scanner._is_link(e):
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                if not (is_dir and skip_dirs):
                    add(e.path, is_dir)
        else:
            add(src, False)
    return units, notes


class Planner:
    def __init__(self, mode, target_root, judge=None, max_laya=300, type_dest=None):
        self.mode = mode
        self.target_root = target_root
        self.judge = judge if judge is not None and judge.status == "ready" else None
        self.max_laya = max_laya
        self.type_dest = type_dest or {}  # 个人规则：某类文件默认放到哪（优先于其他判断）
        self.index = build_index(target_root) if mode == "topic" else []
        self.tops = [f for f in self.index if f["depth"] == 1]
        self.match_list = [f for f in self.index if _usable(f["clean"])]
        self.question = self._laya_question() if self.tops else None

    def _laya_question(self, with_desc=True):
        crit = {}
        for f in self.tops:
            kids = [clean_name(x["name"]) for x in self.index if x["depth"] == 2 and x["top"] == f["name"]][:4]
            desc = f["clean"] + ("：" + "、".join(kids) if kids else "")
            crit[f["name"]] = desc[:30] if with_desc else None
        crit["都不合适"] = "和以上分类都无关" if with_desc else None
        if not with_desc:
            crit = list(crit)
        return {"dest": {"type": "choice", "instructions": "这个文件或文件夹应该放进哪个分类？", "criteria": crit}}

    def _keyword_top(self, text):
        """关键词命中某组，并且这组能对上目标文件夹里的某个分类，返回 (分类, 命中的词)。"""
        for label, words in KEYWORD_GROUPS:
            hit = next((w for w in words.split() if w in text), None)
            if not hit:
                continue
            grams = _bigrams(label)
            for f in self.tops:
                if any(g in f["clean"] for g in grams):
                    return f, hit
        return None, None

    def _options(self, first, extra):
        seen, out = set(), []
        for p in [first] + extra:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def plan(self, units, on_progress, stop):
        items = []
        n_laya = 0
        for i, u in enumerate(units):
            if stop.is_set():
                break
            ext = "" if u["is_dir"] else os.path.splitext(u["name"])[1].lower()
            ftype = "文件夹" if u["is_dir"] else C.file_type(ext)
            it = {"id": i, "path": u["path"], "name": u["name"], "is_dir": u["is_dir"], "size": u["size"],
                  "mtime": u["mtime"], "type": ftype, "dest": None, "via": None, "conf": 0.0,
                  "reason": "", "options": []}
            parent = os.path.dirname(u["path"])
            if self.mode == "type":
                self._plan_type(it, parent)
            else:
                n_laya = self._plan_topic(it, u, ext, n_laya)
            rule_dest = None if u["is_dir"] else self.type_dest.get(ftype)
            if rule_dest:
                it.update(dest=rule_dest, via="rule", conf=1.0, reason=f"你的规则：{ftype}默认放到这里")
                it["options"] = self._options(rule_dest, [o for o in it["options"] if o != rule_dest])
            if it["dest"] and norm(it["dest"]) == norm(parent):
                it["dest"], it["via"], it["reason"] = None, "same", "已经在合适的位置"
            if it["dest"] and under(norm(it["dest"]), norm(u["path"])):
                it["dest"], it["via"], it["reason"] = None, "same", "不能移到自己里面"
            it["status"] = "ok" if it["dest"] else ("skip" if it["via"] in ("same", "keep") else "choose")
            it["options"] = [{"path": p, "label": self._label(p)} for p in it["options"]]
            items.append(it)
            if i % 5 == 0:
                on_progress(f"生成整理方案 {i + 1}/{len(units)}", (i + 1) / max(len(units), 1))
        return items

    def _label(self, p):
        base = self.target_root if self.mode == "topic" else None
        if base and under(norm(p), norm(base)):
            return os.path.relpath(p, base)
        return p

    def _plan_type(self, it, parent):
        base = self.target_root or parent
        all_dirs = [os.path.join(base, TYPE_DIRS.get(t, t)) for t in C.TYPE_EXTS] + [os.path.join(base, "其他")]
        if it["is_dir"]:
            it["via"], it["reason"] = "keep", "文件夹保持不动（按类型只整理散落的文件）"
            it["options"] = all_dirs
            return
        dest = os.path.join(base, TYPE_DIRS.get(it["type"], it["type"]))
        it.update(dest=dest, via="type", conf=1.0, reason=f"按类型：{it['type']}")
        it["options"] = self._options(dest, all_dirs)

    def _plan_topic(self, it, u, ext, n_laya):
        stem = u["name"] if u["is_dir"] else os.path.splitext(u["name"])[0]
        text = (stem + " " + " ".join(u["kids"])).lower()
        matches = []
        for f in self.match_list:
            c = f["clean"].lower()
            if c in text:
                # 名字越长越具体；出现在自己名字里比只出现在子项里可信
                score = len(c) * 10 + f["depth"] + (20 if c in stem.lower() else 0)
                matches.append((score, f))
        matches.sort(key=lambda x: -x[0])
        cand = [f["path"] for _, f in matches[:5]]
        tops = [f["path"] for f in self.tops]
        if matches:
            f = matches[0][1]
            where = "名字" if f["clean"].lower() in stem.lower() else "里面的文件名"
            it.update(dest=f["path"], via="name", conf=0.9 if f["depth"] >= 2 else 0.8,
                      reason=f"{where}里有“{f['clean']}”，与已有文件夹同名")
            it["options"] = self._options(f["path"], cand + tops)
            return n_laya
        top, hit = self._keyword_top(text)
        if top:
            it.update(dest=top["path"], via="keyword", conf=0.7, reason=f"含关键词“{hit}”，归到“{top['clean']}”")
            it["options"] = self._options(top["path"], tops)
            return n_laya
        it["options"] = tops
        if not self.judge or not self.question or n_laya >= self.max_laya:
            it["reason"] = "没有匹配到分类" + ("（超出模型判断数量上限）" if self.judge and n_laya >= self.max_laya else "")
            return n_laya
        if u["is_dir"]:
            content = f"文件夹，内含 {u['n_kids']} 项：" + "；".join(u["kids"][:30])
        else:
            content = extract.snippet(u["path"], ext) or "（未读取内容）"
        state = {"名称": u["name"], "类型": it["type"], "内容": content}
        try:
            ans = self.judge.ask(state, self.question)["dest"]
        except ValueError:
            # 分类太多、描述超长时退回只给名字
            self.question = self._laya_question(with_desc=False)
            try:
                ans = self.judge.ask(state, self.question)["dest"]
            except ValueError:
                self.question = None
                it["reason"] = "分类太多，模型无法判断"
                return n_laya + 1
        choice, p = ans["choice"], ans["probabilities"][ans["choice"]]
        if choice != "都不合适" and p >= 0.5:
            dest = os.path.join(self.target_root, choice)
            it.update(dest=dest, via="laya", conf=round(p, 3), reason=f"Laya 判断放进“{clean_name(choice)}”（{p:.0%}）")
            it["options"] = self._options(dest, tops)
        else:
            it["reason"] = f"Laya 没把握（最像“{clean_name(choice)}”，{p:.0%}）"
        return n_laya + 1


def _unique(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for k in range(2, 1000):
        cand = f"{stem} ({k}){ext}"
        if not os.path.exists(cand):
            return cand
    raise RuntimeError("目标位置重名文件太多")


def _save_history(path, h):
    """先写临时文件再替换，中途断电也不会留下半截的记录。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _new_history_path():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    base = time.strftime("%Y%m%d-%H%M%S")
    hid, k = base, 1
    while os.path.exists(os.path.join(HISTORY_DIR, hid + ".json")):
        k += 1
        hid = f"{base}-{k}"
    return hid, os.path.join(HISTORY_DIR, hid + ".json")


def execute(items, moves):
    """moves: [{id, dest}]。去处必须是方案里给出的选项之一。

    每移动一项之前先把“要从哪移到哪”写进记录，移动失败再删掉这条。
    这样即使程序中途被关掉，已经移走的东西也都在记录里，可以撤销。
    """
    done, failed = [], []
    hid = hpath = None
    h = {"time": time.time(), "total": 0, "records": [], "created_dirs": [], "undone": False, "restored": 0}
    for m in moves:
        it = items[m["id"]] if 0 <= m["id"] < len(items) else None
        try:
            if it is None:
                raise RuntimeError("无效的项目")
            dest = m["dest"]
            if dest not in {o["path"] for o in it["options"]}:
                raise RuntimeError("去处不在方案给出的选项里")
            src = it["path"]
            if C.is_protected(norm(src)) or C.is_protected(norm(dest)):
                raise RuntimeError("涉及受保护目录")
            if under(norm(dest), norm(src)):
                raise RuntimeError("不能移到自己里面")
            if not os.path.exists(src):
                raise RuntimeError("原文件已经不在了")
            if hpath is None:
                hid, hpath = _new_history_path()
            made_dir = not os.path.isdir(dest)
            if made_dir:
                os.makedirs(dest)
                h["created_dirs"].append(dest)
            final = _unique(os.path.join(dest, it["name"]))
            rec = {"src": src, "dst": final}
            h["records"].append(rec)
            h["total"] += 1
            if len(h.setdefault("sample", [])) < 3:
                h["sample"].append(it["name"])
            _save_history(hpath, h)  # 先记下来再动手
            try:
                shutil.move(src, final)
            except Exception:
                h["records"].remove(rec)
                h["total"] -= 1
                if it["name"] in h["sample"]:
                    h["sample"].remove(it["name"])
                if made_dir:
                    try:
                        os.rmdir(dest)
                        h["created_dirs"].remove(dest)
                    except OSError:
                        pass
                _save_history(hpath, h)
                raise
            it["moved_to"] = final
            done.append(it["id"])
        except Exception as e:  # noqa: BLE001
            failed.append({"id": m.get("id"), "error": str(e)})
    if hpath and not h["records"]:
        os.remove(hpath)  # 一项都没移成，不留空记录
        hid = None
    moved = {r_id: items[r_id]["moved_to"] for r_id in done}
    return {"done": done, "moved": moved, "failed": failed, "history": hid}


def list_history(limit=10):
    if not os.path.isdir(HISTORY_DIR):
        return []
    out = []
    names = [fn for fn in os.listdir(HISTORY_DIR) if fn.endswith(".json")]
    for fn in sorted(names, reverse=True)[:limit]:
        try:
            with open(os.path.join(HISTORY_DIR, fn), encoding="utf-8") as f:
                h = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"id": fn[:-5], "time": h["time"], "count": h.get("total", len(h["records"])),
                    "undone": h["undone"], "left": 0 if h["undone"] else len(h["records"]),
                    "partial": bool(h.get("restored")) and not h["undone"],
                    "sample": h.get("sample") or [os.path.basename(r["src"]) for r in h["records"][:3]]})
    return out


def undo(hid):
    """倒序放回原处。放不回去的留在记录里，下次可以再撤销；全部放回后才算撤销完成。"""
    if not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", hid or ""):
        raise ValueError("无效的记录")
    fn = os.path.join(HISTORY_DIR, hid + ".json")
    with open(fn, encoding="utf-8") as f:
        h = json.load(f)
    if h["undone"]:
        return {"restored": 0, "restored_paths": [], "failed": [], "already": True}
    restored, failed, paths, remaining = 0, [], [], []
    for r in reversed(h["records"]):
        src_there, dst_there = os.path.exists(r["src"]), os.path.exists(r["dst"])
        try:
            if dst_there and not src_there:
                os.makedirs(os.path.dirname(r["src"]), exist_ok=True)
                shutil.move(r["dst"], r["src"])
                restored += 1
                paths.append(r["src"])
            elif src_there and not dst_there:
                # 已经在原处了（程序在移动前被关掉，或之前已放回），不用处理
                paths.append(r["src"])
            elif src_there and dst_there:
                raise RuntimeError("原位置和整理后的位置都有这个名字，请手动确认要保留哪个")
            else:
                raise RuntimeError("整理后的文件已经不在了（可能被移动或删除）")
        except Exception as e:  # noqa: BLE001
            failed.append({"name": os.path.basename(r["src"]), "error": str(e)})
            remaining.insert(0, r)
    h["records"] = remaining
    h["restored"] = h.get("restored", 0) + restored
    h["undone"] = not remaining
    if h["undone"]:
        for d in sorted(h.get("created_dirs", []), key=len, reverse=True):
            try:
                os.rmdir(d)  # 只删整理时新建、现在又空了的文件夹
            except OSError:
                pass
    _save_history(fn, h)
    return {"restored": restored, "restored_paths": paths, "failed": failed, "already": False,
            "left": len(remaining)}
