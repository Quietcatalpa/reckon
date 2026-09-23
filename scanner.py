"""遍历磁盘：收集 1MB 以上的文件，把缓存目录整块记为一组，跳过系统和程序目录。"""
import os
import time

import config as C
from config import norm, under

KEEP_MIN_BYTES = 1 << 20  # 1MB 以下的文件不记录（清理价值太小）
FILE_ATTRIBUTE_SYSTEM = 0x4
# 云端占位文件（OneDrive 等）：读取内容会触发下载，只看元数据
CLOUD_ATTRS = 0x400000 | 0x40000 | 0x1000


def _is_link(entry):
    try:
        return entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction())
    except OSError:
        return True


def dir_stats(path, stop=None):
    """统计目录总大小、文件数、最近修改时间（不跟随链接）。"""
    size = count = 0
    newest = 0.0
    stack = [path]
    while stack:
        if stop is not None and stop.is_set():
            break
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if _is_link(e):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            st = e.stat(follow_symlinks=False)
                            size += st.st_size
                            count += 1
                            newest = max(newest, st.st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    return size, count, newest


def _looks_like_program(d, names):
    """已安装/解压的软件目录：有 exe 且有多个 dll，或是 Electron 应用（resources\\app.asar）。"""
    n_exe = sum(1 for n in names if n.endswith(".exe"))
    n_dll = sum(1 for n in names if n.endswith(".dll"))
    if n_exe >= 1 and n_dll >= 3:
        return True
    return "resources" in names and os.path.exists(os.path.join(d, "resources", "app.asar"))


class ScanResult:
    def __init__(self):
        self.files = []       # 1MB 以上的普通文件
        self.groups = {}      # key -> 缓存组
        self.n_files = 0
        self.n_bytes = 0
        self.n_errors = 0
        self.n_programs = 0   # 跳过的程序安装目录数


def _add_group(res, key, label, path, size, count, newest, p, hint):
    g = res.groups.get(key)
    if g is None:
        g = res.groups[key] = {"key": key, "label": label, "paths": [], "size": 0, "count": 0,
                               "newest": 0.0, "p": p, "hint": hint}
    g["paths"].append(path)
    g["size"] += size
    g["count"] += count
    g["newest"] = max(g["newest"], newest)


def _app_label(path, nd):
    """AppData\\Local\\Google\\Chrome\\... -> ('Google\\Chrome', key)。"""
    for base_n in C.APPDATA_NS:
        if under(nd, base_n):
            rel = path[len(base_n) + 1:].split("\\")
            name = "\\".join(rel[:2]) if len(rel) > 2 else rel[0]
            return name, "app:" + name.lower()
    return os.path.basename(path), "app:" + nd


def _scan_temp(res, stop):
    """临时目录：只收 3 天前的顶层条目。"""
    cutoff = time.time() - C.TEMP_MIN_AGE_DAYS * 86400
    try:
        entries = list(os.scandir(C.TEMP))
    except OSError:
        return
    for e in entries:
        if stop.is_set():
            return
        if _is_link(e) or C.is_protected(norm(e.path)):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                size, count, newest = dir_stats(e.path, stop)
                newest = newest or e.stat(follow_symlinks=False).st_mtime
            else:
                st = e.stat(follow_symlinks=False)
                size, count, newest = st.st_size, 1, st.st_mtime
        except OSError:
            continue
        if newest < cutoff:
            _add_group(res, "temp", "系统临时文件", e.path, size, count, newest,
                       0.92, f"%TEMP% 中 {C.TEMP_MIN_AGE_DAYS} 天前的临时文件，程序正在用的会自动跳过")


def _match_group(res, path, nd, name_l, parent_n, stop):
    """若目录属于缓存类，记入组并返回 True（不再深入）。"""
    if nd == C.TEMP_N:
        _scan_temp(res, stop)
        return True
    if nd in C.KNOWN_GROUPS:
        label, p, hint = C.KNOWN_GROUPS[nd]
        _add_group(res, "known:" + label, label, path, *dir_stats(path, stop), p, hint)
        return True
    if name_l == "__pycache__":
        _add_group(res, "pycache", "Python 编译缓存 (__pycache__)", path, *dir_stats(path, stop),
                   0.9, "运行 Python 时会自动重新生成")
        return True
    if name_l == "node_modules":
        size, count, newest = dir_stats(path, stop)
        old = newest and time.time() - newest > 180 * 86400
        _add_group(res, "nm:" + nd, "node_modules · " + os.path.basename(os.path.dirname(path)),
                   path, size, count, newest, 0.72 if old else 0.55,
                   "前端依赖目录，可用 npm install 重新生成")
        return True
    if parent_n == norm(C.HF_HUB) and (name_l.startswith("models--") or name_l.startswith("datasets--")):
        kind = "模型" if name_l.startswith("models--") else "数据集"
        label = f"HuggingFace {kind} · " + path.split("--", 1)[1].replace("--", "/")
        _add_group(res, "hf:" + nd, label, path, *dir_stats(path, stop), 0.5,
                   f"下载过的{kind}缓存，删除后用到时会重新下载")
        return True
    if parent_n == norm(C.HOME_CACHE) and name_l not in ("huggingface", "pip"):
        _add_group(res, "hc:" + nd, "工具缓存 · .cache\\" + os.path.basename(path), path,
                   *dir_stats(path, stop), 0.45, "各类工具的下载/模型缓存，用途请自行确认")
        return True
    if name_l in C.CACHE_DIR_NAMES and any(under(nd, b) for b in C.APPDATA_NS):
        app, key = _app_label(path, nd)
        _add_group(res, key, "应用缓存 · " + app, path, *dir_stats(path, stop), 0.85,
                   "应用缓存和日志，删除后会自动重新生成；程序运行中的文件可能删不掉")
        return True
    return False


def scan(roots, on_progress, stop):
    res = ScanResult()
    stack = list(roots)
    last = time.time()
    while stack:
        if stop.is_set():
            break
        d = stack.pop()
        nd = norm(d)
        if C.is_protected(nd):
            continue
        parent_n = os.path.dirname(nd)
        name_l = os.path.basename(nd)
        if _match_group(res, d, nd, name_l, parent_n, stop):
            continue
        try:
            entries = list(os.scandir(d))
        except OSError:
            res.n_errors += 1
            continue
        names = {e.name.lower() for e in entries}
        if "conda-meta" in names:
            pk = os.path.join(d, "pkgs")
            if os.path.isdir(pk):
                _add_group(res, "conda:" + nd, "conda 安装包缓存 · " + d, pk, *dir_stats(pk, stop), 0.6,
                           "建议在命令行运行 conda clean -a 清理，而不是直接删除")
            res.n_programs += 1
            continue
        if names & C.UNINSTALL_MARKERS or _looks_like_program(d, names):
            if under(nd, C.DOWNLOADS_N) and nd != C.DOWNLOADS_N:
                # 下载文件夹里解压出来的软件：整个文件夹作为一项让用户决定
                _add_group(res, "dlprog:" + nd, "下载文件夹中的软件 · " + os.path.basename(d), d,
                           *dir_stats(d, stop), 0.6, "看起来是解压出来的软件，确认不再使用可以整个删除")
            res.n_programs += 1
            continue
        is_root = len(nd.rstrip("\\").split("\\")) == 1
        for e in entries:
            if _is_link(e):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in C.SKIP_DIR_NAMES:
                        stack.append(e.path)
                    continue
                if is_root and e.name.lower() in C.ROOT_SKIP_FILES:
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                res.n_errors += 1
                continue
            res.n_files += 1
            res.n_bytes += st.st_size
            attrs = getattr(st, "st_file_attributes", 0)
            if st.st_size >= KEEP_MIN_BYTES and not attrs & FILE_ATTRIBUTE_SYSTEM:
                res.files.append({
                    "path": e.path, "size": st.st_size, "mtime": st.st_mtime,
                    "cloud": bool(attrs & CLOUD_ATTRS),
                })
        now = time.time()
        if now - last > 0.5:
            last = now
            on_progress(f"已扫描 {res.n_files:,} 个文件 · {d}")
    return res
