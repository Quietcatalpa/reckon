"""重复文件：先按大小分组，再比首尾 64KB，最后比完整哈希（超大文件抽样比对）。"""
import hashlib
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import config as C
from .config import norm, under, under_any

CHUNK = 1 << 20
EDGE = 64 * 1024
FULL_HASH_LIMIT = 256 * CHUNK  # 256MB 以上的文件抽样 16 块比对
COPY_NAME = re.compile(r"(\(\d+\)|（\d+）|副本|copy|- 复制)", re.I)


def _quick(path, size):
    with open(path, "rb") as f:
        h = hashlib.blake2b(f.read(EDGE), digest_size=16)
        if size > 2 * EDGE:
            f.seek(-EDGE, os.SEEK_END)
            h.update(f.read(EDGE))
    return h.hexdigest()


def _full(path, size):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        if size <= FULL_HASH_LIMIT:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
            return h.hexdigest(), True
        for i in range(16):
            f.seek(size * i // 16)
            h.update(f.read(CHUNK))
    return h.hexdigest(), False


def same_content(a, b, stop=None):
    """逐块完整比对两个文件，遇到第一处不同就停。删除重复副本前用它做最终确认（不依赖扫描时的抽样结果）。"""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            if stop is not None and stop.is_set():
                return False
            x, y = fa.read(4 * CHUNK), fb.read(4 * CHUNK)
            if x != y:
                return False
            if not x:
                return True


def _keep_score(f, prefer=()):
    """分数越高越应该保留：你设置的“永远保留”文件夹最优先，其次整理过的个人目录；下载/临时目录和“副本”命名靠后。"""
    np_ = norm(f["path"])
    s = 5 if any(under(np_, p) for p in prefer) else 0
    if under(np_, C.TEMP_N):
        s -= 3
    if under(np_, C.DOWNLOADS_N):
        s -= 2
    if under_any(np_, C.PERSONAL_NS):
        s += 1
    if COPY_NAME.search(os.path.basename(f["path"])):
        s -= 1
    return (s, -f["mtime"], -len(f["path"]))


WORKERS = 4  # 读文件算指纹主要在等磁盘，hashlib 算大块数据时会释放 GIL，多线程能同时读好几个


def _hash_all(func, files, stop, on_each=None):
    """并行计算，返回 {path: 结果}；读不了的文件不在结果里。"""
    out = {}

    def one(f):
        if stop.is_set():
            return f, None
        try:
            return f, func(f["path"], f["size"])
        except OSError:
            return f, None

    with ThreadPoolExecutor(WORKERS) as ex:
        for f, v in ex.map(one, files):
            if v is not None:
                out[f["path"]] = v
            if on_each:
                on_each()
    return out


def find_duplicates(files, on_progress, stop, prefer_dirs=()):
    by_size = defaultdict(list)
    for f in files:
        np_ = norm(f["path"])
        if f["cloud"] or under_any(np_, C.APPDATA_NS):
            continue
        # 程序组件在不同软件里重复很正常，删了会坏；下载文件夹里的重复安装包照常处理
        if os.path.splitext(np_)[1] in C.PROGRAM_EXTS and not under(np_, C.DOWNLOADS_N):
            continue
        by_size[f["size"]].append(f)
    cands = [f for g in by_size.values() if len(g) > 1 for f in g]
    # 第一步：所有同大小的文件并行算首尾 64KB 的指纹
    done = [0]

    def tick():
        done[0] += 1
        if done[0] % 50 == 0:
            on_progress(f"比对重复文件 {done[0]:,}/{len(cands):,}", done[0] / max(len(cands), 1) * 0.5)

    quick = _hash_all(_quick, cands, stop, tick)
    by_quick = defaultdict(list)
    for f in cands:
        if f["path"] in quick:
            by_quick[(f["size"], quick[f["path"]])].append(f)
    # 第二步：大小和首尾都一样的，再并行算完整指纹（超大文件抽样）
    second = [f for same in by_quick.values() if len(same) > 1 for f in same]
    done2 = [0]

    def tick2():
        done2[0] += 1
        if done2[0] % 20 == 0:
            on_progress(f"完整比对疑似重复 {done2[0]:,}/{len(second):,}", 0.5 + done2[0] / max(len(second), 1) * 0.5)

    full = _hash_all(_full, second, stop, tick2)
    by_full = defaultdict(list)
    for f in second:
        if f["path"] in full:
            digest, exact = full[f["path"]]
            by_full[(f["size"], digest, exact)].append(f)
    groups = []
    for (size, digest, exact), copies in by_full.items():
        if len(copies) < 2:
            continue
        prefer = [norm(d) for d in prefer_dirs]
        copies.sort(key=lambda f: _keep_score(f, prefer), reverse=True)
        groups.append({"hash": digest, "size": size, "exact": exact,
                       "keep": copies[0]["path"], "copies": [c["path"] for c in copies[1:]]})
    on_progress(f"比对重复文件完成，找到 {len(groups)} 组", 1.0)
    return groups
