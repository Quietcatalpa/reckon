"""打包后的冒烟测试：启动 dist/Reckon/Reckon.exe，确认页面和接口能用、模型能加载。

    python scripts/smoke_exe.py

· 启动时把 PATH 精简到只剩 Windows 系统目录、去掉 PYTHON* 环境变量，模拟“没装 Python”的电脑；
  这和一台真正干净的电脑并不完全一样（比如 VC++ 运行库、字体、杀毒软件），发布前最好再在别的电脑上试一次。
· 数据目录指到临时文件夹，不读写本机的扫描结果和个人规则，也不扫描、不删除、不整理任何文件。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "Reckon", "Reckon.exe")


def clean_env(data_dir):
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV"))}
    env["PATH"] = os.pathsep.join([os.path.join(sysroot, "System32"), sysroot])
    env["RECKON_DATA"] = data_dir
    return env


def get(url, token=None, timeout=3):
    req = urllib.request.Request(url, headers={"X-Token": token} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def start(args, port, data_dir):
    proc = subprocess.Popen([EXE, "--no-browser", "--port", str(port)] + args, env=clean_env(data_dir),
                            cwd=tempfile.gettempdir(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError("程序退出了：\n" + proc.stdout.read().decode("utf-8", "replace"))
        try:
            html = get(url)
            token = re.search(r'const TOKEN = "([^"]+)"', html).group(1)
            return proc, url, token
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("60 秒内没有打开页面")


def check(name, args, port, want_model, wait=240):
    data_dir = tempfile.mkdtemp(prefix="reckon_smoke_")
    t0 = time.time()
    proc, url, token = start(args, port, data_dir)
    try:
        print(f"[{name}] 页面已打开 {url}（{time.time() - t0:.1f} 秒）")
        try:
            get(url + "api/state")
            raise RuntimeError("没带令牌也能访问接口")
        except urllib.error.HTTPError as e:
            assert e.code == 403, e.code
        deadline = time.time() + wait
        while True:
            st = json.loads(get(url + "api/state", token))
            model = st["model"]
            if model["status"] != "loading" or time.time() > deadline:
                break
            time.sleep(2)
        print(f"[{name}] 模型：{model}，盘符：{[d['root'] if isinstance(d, dict) else d for d in st['drives']]}"
              f"（{time.time() - t0:.1f} 秒）")
        if model["status"] != want_model:
            raise RuntimeError(f"模型状态应当是 {want_model}，实际是 {model}")
        if want_model == "ready" and model.get("backend") != "onnx":
            raise RuntimeError(f"exe 应当用 ONNX 模型，实际是 {model.get('backend')}")
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(data_dir, ignore_errors=True)


def main():
    if not os.path.exists(EXE):
        sys.exit(f"没有 {EXE}，先运行 python scripts/build_exe.py")
    check("演示模式", ["--demo"], 8791, "demo")
    check("正常模式", [], 8792, "ready")
    print("通过（精简 PATH 模拟没装 Python 的环境；不等同于一台干净的电脑）")


if __name__ == "__main__":
    main()
