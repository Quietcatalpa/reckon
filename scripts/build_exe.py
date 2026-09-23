"""打包 Windows 版：dist/Reckon/Reckon.exe + 模型，再压成 dist/Reckon-<版本>-win64.zip。

    python scripts/build_exe.py

步骤：
1. 在 build/venv 里建一个干净的虚拟环境，只装 scripts/requirements-exe.txt 里的依赖（不带 torch，体积小）；
2. 用 PyInstaller 打包（文件夹形式，启动快，也不会被当成自解压程序拦截）；
3. 把 build/laya-onnx 里的半精度模型拷进 dist/Reckon/model（没有就先运行 scripts/export_onnx.py）；
4. 带上许可证和说明，压缩成 zip。
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from reckon import __version__  # noqa: E402

BUILD = os.path.join(ROOT, "build")
VENV = os.path.join(BUILD, "venv")
DIST = os.path.join(ROOT, "dist")
APP = os.path.join(DIST, "Reckon")
MODEL_SRC = os.path.join(BUILD, "laya-onnx")
MODEL_FILES = ("model.fp16.onnx", "tokenizer.json", "laya_meta.json")


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    missing = [f for f in MODEL_FILES if not os.path.exists(os.path.join(MODEL_SRC, f))]
    if missing:
        sys.exit(f"缺少 {missing}，先运行 python scripts/export_onnx.py")
    py = os.path.join(VENV, "Scripts", "python.exe")
    if not os.path.exists(py):
        run(sys.executable, "-m", "venv", VENV)
    run(py, "-m", "pip", "install", "-q", "--upgrade", "pip")
    run(py, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "scripts", "requirements-exe.txt"))

    shutil.rmtree(APP, ignore_errors=True)
    entry = os.path.join(BUILD, "reckon_entry.py")
    with open(entry, "w", encoding="utf-8") as f:
        f.write("from reckon.server import main\n\nmain()\n")
    web, demo = os.path.join(ROOT, "reckon", "web"), os.path.join(ROOT, "reckon", "demo")
    run(py, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "Reckon", "--console",
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"), "--specpath", BUILD,
        "--paths", ROOT,
        "--add-data", f"{web}{os.pathsep}reckon/web", "--add-data", f"{demo}{os.pathsep}reckon/demo",
        "--collect-submodules", "reckon",
        "--hidden-import", "send2trash.win.modern", "--hidden-import", "win32com.shell.shell",
        "--hidden-import", "onnx", "--hidden-import", "onnx.numpy_helper",
        "--exclude-module", "torch", "--exclude-module", "transformers", "--exclude-module", "laya",
        "--exclude-module", "fitz", "--exclude-module", "pymupdf",
        entry)

    model_dir = os.path.join(APP, "model")
    os.makedirs(model_dir, exist_ok=True)
    for fn in MODEL_FILES:
        shutil.copy(os.path.join(MODEL_SRC, fn), model_dir)
    for fn in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy(os.path.join(ROOT, fn), APP)
    with open(os.path.join(APP, "使用说明.txt"), "w", encoding="utf-8-sig") as f:
        f.write(f"""盘算 Reckon {__version__}

双击 Reckon.exe 启动，浏览器会打开 http://127.0.0.1:8765/ 。关闭黑色窗口就退出。

· 整理某个文件夹：把它拖到 Reckon.exe 上。
· 在右键「发送到」菜单里加上「盘算 - 整理」：在这个文件夹里打开命令行，运行  Reckon.exe --install-sendto
  （去掉：Reckon.exe --remove-sendto）
· 先看看效果、不碰任何文件：Reckon.exe --demo

扫描结果、整理记录、个人规则存在 %LOCALAPPDATA%\\Reckon 里；删除这个文件夹就清空了所有记录。
清理只会移到回收站，整理可以一键撤销。model 文件夹是 Laya 决策模型（Apache-2.0），不要删除，否则只能按规则判断。

说明和源码：https://github.com/Quietcatalpa/reckon
""")

    zip_path = os.path.join(DIST, f"Reckon-{__version__}-win64.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _, files in os.walk(APP):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.join("Reckon", os.path.relpath(full, APP)))
    size = lambda p: sum(os.path.getsize(os.path.join(b, f)) for b, _, fs in os.walk(p) for f in fs)
    print(f"完成：{APP}（{size(APP) / 1e6:.0f} MB），{zip_path}（{os.path.getsize(zip_path) / 1e6:.0f} MB）")


if __name__ == "__main__":
    main()
