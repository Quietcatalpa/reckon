"""重复文件：先按大小分组，再比首尾 64KB，最后比完整哈希（超大文件抽样比对）。"""
import hashlib
import os
import re
from collections import defaultdict

import config as C
from config import norm, under, under_any

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


def _keep_score(f):
    """分数越高越应该保留：整理过的个人目录优先，下载/临时目录和“副本”命名靠后。"""
    np_ = norm(f["path"])
    s = 0
    if under(np_, C.TEMP_N):
        s -= 3
    if under(np_, C.DOWNLOADS_N):
        s -= 2
    if under_any(np_, C.PERSONAL_NS):
        s += 1
    if COPY_NAME.search(os.path.basename(f["path"])):
        s -= 1
    return (s, -f["mtime"], -len(f["path"]))


def find_duplicates(files, on_progress, stop):
    by_size = defaultdict(list)
    for f in files:
        np_ = norm(f["path"])
        if f["cloud"] or under_any(np_, C.APPDATA_NS):
            continue
        # 程序组件在不同软件里重复很正常，删了会坏；下载文件夹里的重复安装包照常处理
        if os.path.splitext(np_)[1] in C.PROGRAM_EXTS and not under(np_, C.DOWNLOADS_N):
            continue
        by_size[f["size"]].append(f)
    buckets = [g for g in by_size.values() if len(g) > 1]
    total = sum(len(g) for g in buckets)
    done = 0
    groups = []
    for bucket in buckets:
        if stop.is_set():
            break
        by_quick = defaultdict(list)
        for f in bucket:
            done += 1
            try:
                by_quick[_quick(f["path"], f["size"])].append(f)
            except OSError:
                pass
        for same in by_quick.values():
            if len(same) < 2:
                continue
            by_full = defaultdict(list)
            for f in same:
                if stop.is_set():
                    break
                try:
                    digest, exact = _full(f["path"], f["size"])
                except OSError:
                    continue
                by_full[digest].append(f)
            for digest, copies in by_full.items():
                if len(copies) < 2:
                    continue
                copies.sort(key=_keep_score, reverse=True)
                groups.append({"hash": digest, "size": copies[0]["size"], "exact": exact,
                               "keep": copies[0]["path"], "copies": [c["path"] for c in copies[1:]]})
        on_progress(f"比对重复文件 {done:,}/{total:,}", done / max(total, 1))
    return groups
