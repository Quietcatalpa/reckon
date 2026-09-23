"""生成演示模式用的虚构数据（python -m reckon --demo 时加载，也用来给 README 截图）。

所有路径、文件名、分类和数字都是编的：一个虚构的上班族“demo”的电脑，不对应任何真实的人或电脑。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "reckon", "demo")  # 演示数据随程序一起发布
NOW = 1790000000.0  # 固定时间，保证每次生成的数据一样
GB, MB = 1 << 30, 1 << 20
DL = r"D:\Downloads"
LIB = r"D:\资料"


def rec(p):
    return "delete" if p >= 0.7 else "review" if p >= 0.4 else "keep"


items = []


def add(kind, name, path, size, age, ftype, p, reasons, topic=None, laya=None, **kw):
    if kind == "file":
        # 和真实扫描一样记下规则分：有模型判断时反推出规则那一半（清理建议分 = 规则 × 45% + Laya × 55%）
        rule = p if not laya else min(0.98, max(0.05, (p - 0.55 * laya["delete"]) / 0.45))
        if laya:
            p = round(0.45 * rule + 0.55 * laya["delete"], 3)
        kw.setdefault("rule", round(rule, 3))
    it = {"id": len(items), "kind": kind, "name": name, "path": path, "size": int(size),
          "mtime": NOW - age * 86400, "age": age, "type": ftype, "topic": topic, "p": p, "rec": rec(p),
          "reasons": reasons, "laya": laya}
    it.update(kw)
    items.append(it)
    return it["id"]


def group(label, path, size, count, p, hint, age=0, n_paths=1, strict=False):
    add("group", label, path, size, age, "缓存目录", p, [hint, f"{count:,} 个文件", "今天修改过" if age < 1 else f"{age} 天未修改"],
        paths=[path] * n_paths, count=count, strict=strict)


def laya(d, read=True):
    return {"delete": d, "read": read, "topic": None, "topic_p": 0}


group("系统临时文件", r"C:\Users\demo\AppData\Local\Temp\7zO1A2B", 2.3 * GB, 18421, 0.92,
      "%TEMP% 中 3 天前的临时文件，程序正在用的会自动跳过", age=41, n_paths=37, strict=True)
group("pip 下载缓存", r"C:\Users\demo\AppData\Local\pip\cache", 2.0 * GB, 912, 0.92, "pip 安装包缓存，需要时会重新下载")
group("应用缓存 · Google\\Chrome", r"C:\Users\demo\AppData\Local\Google\Chrome\User Data\Default\Cache", 1.8 * GB, 20311,
      0.85, "应用缓存和日志，删除后会自动重新生成；程序运行中的文件可能删不掉", n_paths=9)
group("应用缓存 · Code", r"C:\Users\demo\AppData\Roaming\Code\logs", 1.2 * GB, 3120, 0.85,
      "应用缓存和日志，删除后会自动重新生成；程序运行中的文件可能删不掉", n_paths=6)
group("node_modules · my-blog", r"D:\code\my-blog\node_modules", 0.35 * GB, 41210, 0.72,
      "前端依赖目录，可用 npm install 重新生成", age=260)
group("HuggingFace 模型 · bert-base-chinese", r"C:\Users\demo\.cache\huggingface\hub\models--bert-base-chinese",
      0.4 * GB, 12, 0.5, "下载过的模型缓存，删除后用到时会重新下载", age=190)

add("file", "ubuntu-24.04-desktop-amd64.iso", DL + r"\ubuntu-24.04-desktop-amd64.iso", 5.7 * GB, 214, "安装包", 0.88,
    ["位于下载文件夹", "安装包，已放了 214 天", "214 天未修改"], topic="软件工具")
add("file", "Anaconda3-2024.10-Windows-x86_64.exe", DL + r"\Anaconda3-2024.10-Windows-x86_64.exe", 0.9 * GB, 330, "安装包",
    0.88, ["位于下载文件夹", "安装包，已放了 330 天", "330 天未修改"], topic="软件工具")
add("file", "修图软件_安装包.zip", DL + r"\修图软件_安装包.zip", 1.2 * GB, 120, "压缩包", 0.83,
    ["位于下载文件夹", "旁边已有解压出来的同名文件夹", "120 天未修改", "Laya：倾向删除 71%（已读内容）"],
    topic="软件工具", laya=laya(0.71))
add("file", "SteamSetup.exe", DL + r"\SteamSetup.exe", 0.18 * GB, 446, "安装包", 0.88,
    ["位于下载文件夹", "安装包，已放了 446 天", "446 天未修改"], topic="软件工具")
add("file", "app-2024-11.log", r"D:\tools\server\logs\app-2024-11.log", 0.21 * GB, 300, "日志缓存", 0.86,
    ["临时/日志/残留下载类文件", "300 天未修改", "Laya：倾向删除 75%（已读内容）"], laya=laya(0.75))
add("file", "WeChatSetup.exe", DL + r"\WeChatSetup.exe", 0.25 * GB, 95, "安装包", 0.88,
    ["位于下载文件夹", "安装包，已放了 95 天", "95 天未修改"], topic="软件工具")
add("file", "机器学习入门.pdf", DL + r"\机器学习入门.pdf", 0.18 * GB, 252, "文档", 0.54,
    ["位于下载文件夹", "252 天未修改", "Laya：倾向删除 44%（已读内容）"], laya=laya(0.44))
add("file", "屏幕录制 2025-03-02.mp4", DL + r"\屏幕录制 2025-03-02.mp4", 1.1 * GB, 402, "视频", 0.45,
    ["位于下载文件夹", "超过一年未修改的媒体文件", "402 天未修改"])
add("file", "dataset_2025.parquet", DL + r"\dataset_2025.parquet", 0.35 * GB, 70, "数据", 0.52,
    ["位于下载文件夹", "70 天未修改", "Laya：倾向删除 47%（仅看文件信息）"], laya=laya(0.47, False))
add("file", "年终总结_终稿.docx", r"C:\Users\demo\Desktop\年终总结_终稿.docx", 26 * MB, 12, "文档", 0.06,
    ["位于个人资料目录", "12 天未修改", "Laya：倾向删除 1%（已读内容）"], laya=laya(0.01))
add("file", "raw_2024.csv", r"D:\资料\01_工作项目\销售分析\data\raw_2024.csv", 0.3 * GB, 380, "数据", 0.28,
    ["380 天未修改", "Laya：倾向删除 12%（已读内容）"], laya=laya(0.12))
add("file", "摄影后期教程.pdf", DL + r"\摄影后期教程.pdf", 0.17 * GB, 435, "文档", 0.32,
    ["位于下载文件夹", "435 天未修改", "Laya：倾向删除 0%（已读内容）"], laya=laya(0.0))

dupes = []


def dupe(keep, copies, size):
    ids = []
    for cp in copies:
        ids.append(add("dupe", os.path.basename(cp), cp, size, 180, "文档" if cp.endswith(".pdf") else "压缩包", 0.88,
                       ["与保留的那份内容完全相同"], dup_of=keep, dup_group=len(dupes)))
    dupes.append({"keep": keep, "size": int(size), "exact": True, "ids": ids})


dupe(LIB + r"\03_生活账单\租房合同.pdf",
     [r"C:\Users\demo\Documents\WeChat Files\wxid_demo\FileStorage\File\2025-03\租房合同.pdf"], 38 * MB)
dupe(LIB + r"\06_设计素材\壁纸合集.zip", [DL + r"\壁纸合集 (1).zip", DL + r"\壁纸合集 (2).zip"], 0.45 * GB)

scan = {"finished_at": NOW, "seconds": 312, "drives": ["C:\\", "D:\\"], "stopped": False, "used_laya": True,
        "stats": {"files": 186420, "bytes": int(256 * GB), "errors": 2, "programs": 47},
        "items": items, "dupes": dupes}

# ---- 整理方案 ----
tops = ["01_工作项目", "02_学习笔记", "03_生活账单", "04_旅行", "05_照片视频", "06_设计素材", "07_归档"]
plan_items = []


def org(name, dest_rel, via, conf, reason, ftype, size, is_dir=False, status=None):
    dest = os.path.join(LIB, dest_rel) if dest_rel else None
    opts = ([dest] if dest else []) + [os.path.join(LIB, t) for t in tops]
    seen, options = set(), []
    for o in opts:
        if o not in seen:
            seen.add(o)
            options.append({"path": o, "label": os.path.relpath(o, LIB)})
    plan_items.append({"id": len(plan_items), "path": DL + "\\" + name, "name": name, "is_dir": is_dir, "size": int(size),
                       "mtime": NOW - 30 * 86400, "type": ftype, "dest": dest, "via": via, "conf": conf,
                       "reason": reason, "options": options, "status": status or ("ok" if dest else "choose")})


org("三亚旅行攻略.pdf", r"04_旅行\三亚", "name", 0.9, "名字里有“三亚”，与已有文件夹同名", "文档", 12 * MB)
org("客户提案_v3.pptx", r"01_工作项目\客户提案", "name", 0.9, "名字里有“客户提案”，与已有文件夹同名", "文档", 18 * MB)
org("周报_第38周.docx", r"01_工作项目\周报", "name", 0.9, "名字里有“周报”，与已有文件夹同名", "文档", 0.3 * MB)
org("云南照片精选", r"04_旅行\云南", "name", 0.9, "名字里有“云南”，与已有文件夹同名", "文件夹", 850 * MB, True)
org("前端 React 入门笔记.md", r"02_学习笔记\前端", "name", 0.9, "名字里有“前端”，与已有文件夹同名", "文档", 0.1 * MB)
org("电费发票_2025-06.pdf", r"03_生活账单\发票", "name", 0.9, "名字里有“发票”，与已有文件夹同名", "文档", 0.2 * MB)
org("旧房屋合同扫描.pdf", "03_生活账单", "keyword", 0.7, "含关键词“合同”，归到“生活账单”", "文档", 6 * MB)
org("图标素材包.zip", "06_设计素材", "keyword", 0.7, "含关键词“素材”，归到“设计素材”", "压缩包", 230 * MB)
org("摄影构图技巧.pdf", "02_学习笔记", "laya", 0.78, "Laya 判断放进“学习笔记”（78%）", "文档", 9 * MB)
org("随手记.txt", None, None, 0.0, "Laya 没把握（最像“都不合适”，76%）", "文档", 0.01 * MB)

plan = {"mode": "topic", "target": LIB, "sources": [DL], "items": plan_items, "notes": [], "used_laya": True,
        "created": NOW}

for fn, data in (("scan.json", scan), ("plan.json", plan)):
    with open(os.path.join(OUT_DIR, fn), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
print("ok", len(items), "items,", len(plan_items), "plan items")
