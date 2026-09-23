"""读取“容易读”的文件内容片段，交给 Laya 参考。读不了就返回 None。"""
import json
import os
import re
import threading
import zipfile

TEXT_EXTS = set(
    ".txt .md .log .csv .tsv .json .xml .yaml .yml .ini .cfg .toml .tex .bib .py .js .ts .jsx .tsx "
    ".java .c .cpp .h .hpp .m .r .go .rs .cs .html .css .sql .sh .bat .ps1".split()
)
READABLE_EXTS = TEXT_EXTS | {".docx", ".pptx", ".xlsx", ".xlsm", ".pdf", ".zip", ".ipynb"}
_WS = re.compile(r"\s+")


def _clean(s, limit):
    return _WS.sub(" ", s).strip()[:limit] or None


def _text(path, limit):
    with open(path, "rb") as f:
        raw = f.read(limit * 4)
    if b"\x00" in raw[:1024]:
        return None  # 其实是二进制
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def _docx(path, limit):
    from docx import Document
    out, n = [], 0
    for para in Document(path).paragraphs:
        if para.text.strip():
            out.append(para.text)
            n += len(para.text)
            if n > limit:
                break
    return "\n".join(out)


def _pptx(path, limit):
    from pptx import Presentation
    out, n = [], 0
    for i, slide in enumerate(Presentation(path).slides):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                out.append(shape.text_frame.text)
                n += len(out[-1])
        if n > limit or i > 10:
            break
    return "\n".join(out)


def _xlsx(path, limit):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out = ["工作表：" + "、".join(wb.sheetnames[:10])]
        for row in wb.worksheets[0].iter_rows(max_row=12, values_only=True):
            cells = [str(v) for v in row[:12] if v is not None]
            if cells:
                out.append(" | ".join(cells))
        return "\n".join(out)
    finally:
        wb.close()


def _pdf(path, limit):
    import fitz
    with fitz.open(path) as doc:
        out = []
        title = (doc.metadata or {}).get("title")
        if title:
            out.append("标题：" + title)
        out.append(f"共 {doc.page_count} 页")
        for page in doc.pages(0, min(2, doc.page_count)):
            out.append(page.get_text())
        return "\n".join(out)


def _zip(path, limit):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    return f"压缩包内共 {len(names)} 个文件：" + "；".join(names[:40])


def _ipynb(path, limit):
    with open(path, encoding="utf-8") as f:
        nb = json.load(f)
    return "\n".join("".join(c.get("source", "")) for c in nb.get("cells", [])[:8])


_READERS = {".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx, ".xlsm": _xlsx,
            ".pdf": _pdf, ".zip": _zip, ".ipynb": _ipynb}


# 这些格式要把整个文件（或整个压缩包里的 XML）读进内存才能解析，太大就只看文件信息
SIZE_LIMITS = {".docx": 100 << 20, ".pptx": 100 << 20, ".xlsx": 100 << 20, ".xlsm": 100 << 20,
               ".ipynb": 20 << 20, ".pdf": 300 << 20}
TIMEOUT = 8  # 单个文件解析超过这么多秒就放弃，免得一个怪文件拖住整个扫描


def _run_with_timeout(fn, *args):
    """在后台线程里解析，超时就不等了（线程没法强行结束，但会在后台自己跑完，不影响后续文件）。"""
    box = {}

    def work():
        try:
            box["v"] = fn(*args)
        except Exception:  # noqa: BLE001
            box["v"] = None

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(TIMEOUT)
    return box.get("v")


def snippet(path, ext, limit=600):
    if ext not in READABLE_EXTS:
        return None
    reader = _READERS.get(ext, _text)
    try:
        if os.path.getsize(path) > SIZE_LIMITS.get(ext, float("inf")):
            return None
    except OSError:
        return None
    text = _run_with_timeout(reader, path, limit)
    try:
        return _clean(text, limit)
    except Exception:  # noqa: BLE001
        return None
