"""本地网页服务：只监听 127.0.0.1。清理：查看建议、勾选后移到回收站；整理：生成方案、确认后移动、可撤销。

用法：python server.py [--port 8765] [--no-laya] [--no-browser] [--organize 路径 ...]
"""
import argparse
import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config as C
import decide
import dupes
import organize
import scanner
from config import norm

WEB_DIR = os.path.join(C.TOOL_DIR, "web")
TOKEN = secrets.token_urlsafe(24)


DEMO_DIR = os.path.join(C.TOOL_DIR, "docs", "demo")
# 演示模式：清理和整理只在内存里模拟，界面照常变化，磁盘上什么都不动


class App:
    def __init__(self, use_laya, demo=False):
        self.demo = demo
        self.judge = decide.LayaJudge() if use_laya and not demo else None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.job = {"running": False, "kind": None, "phase": "idle", "message": "", "progress": 0.0, "error": None}
        self.results = None
        self.plan = None
        self.demo_history = []
        if demo:
            with open(os.path.join(DEMO_DIR, "scan.json"), encoding="utf-8") as f:
                self.results = json.load(f)
            with open(os.path.join(DEMO_DIR, "plan.json"), encoding="utf-8") as f:
                self.plan = json.load(f)
        elif os.path.exists(C.RESULTS_FILE):
            try:
                with open(C.RESULTS_FILE, encoding="utf-8") as f:
                    self.results = json.load(f)
            except (OSError, ValueError):
                pass
        if self.judge:
            threading.Thread(target=self.judge.load, daemon=True).start()

    def model_info(self):
        if self.demo:
            return {"status": "demo", "error": None}
        if self.judge is None:
            return {"status": "disabled", "error": None}
        return {"status": self.judge.status, "error": self.judge.error}

    def _set(self, **kw):
        with self.lock:
            self.job.update(kw)

    def _start(self, kind, target, opts, message):
        if self.demo:
            return False
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"running": True, "kind": kind, "phase": "running", "message": message,
                        "progress": 0.0, "error": None}
        self.stop.clear()
        threading.Thread(target=target, args=(opts,), daemon=True).start()
        return True

    def start_scan(self, opts):
        return self._start("scan", self._run, opts, "准备扫描…")

    def start_organize(self, opts):
        return self._start("organize", self._run_organize, opts, "读取要整理的内容…")

    def _run_organize(self, opts):
        try:
            mode = "type" if opts.get("mode") == "type" else "topic"
            target = (opts.get("target") or "").strip().strip('"')
            if target:
                target = os.path.abspath(target)
                if not os.path.isdir(target) or C.is_protected(norm(target)):
                    raise ValueError("目标文件夹不存在或不允许使用")
            elif mode == "topic":
                raise ValueError("按主题整理需要先选一个目标文件夹")
            units, notes = organize.collect_units(opts.get("sources", []), target or None)
            judge = None
            if mode == "topic" and self.judge and opts.get("use_laya", True):
                if not self.judge.ready.is_set():
                    self._set(message="等待 Laya 模型加载完成…")
                    self.judge.ready.wait()
                judge = self.judge
            planner = organize.Planner(mode, target or None, judge, int(opts.get("max_laya", 300)))
            if mode == "topic" and not planner.tops:
                raise ValueError("目标文件夹里没有子文件夹，没有可以放进去的分类")
            items = planner.plan(units, lambda m, p: self._set(message=m, progress=p), self.stop)
            self.plan = {"mode": mode, "target": target, "sources": opts.get("sources", []), "items": items, "notes": notes,
                         "used_laya": planner.judge is not None, "created": time.time()}
            self._set(running=False, phase="done", progress=1.0, message=f"整理方案已生成，共 {len(items)} 项")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(running=False, phase="error", error=str(e), message="生成整理方案出错")

    def run_organize(self, moves):
        if self.demo:
            items = self.plan["items"]
            moved = {m["id"]: os.path.join(m["dest"], items[m["id"]]["name"]) for m in moves if 0 <= m["id"] < len(items)}
            for iid, to in moved.items():
                items[iid]["moved_to"] = to
            hid = time.strftime("%Y%m%d-%H%M%S")
            self.demo_history.insert(0, {"id": hid, "time": time.time(), "count": len(moved), "undone": False,
                                         "sample": [items[i]["name"] for i in list(moved)[:3]], "ids": list(moved)})
            return {"done": list(moved), "moved": moved, "failed": [], "history": hid, "demo": True}
        if not self.plan:
            return {"done": [], "moved": {}, "failed": [{"id": None, "error": "还没有整理方案"}], "history": None}
        with self.lock:
            if self.job["running"]:
                return {"done": [], "moved": {}, "failed": [{"id": None, "error": "有任务正在进行"}], "history": None}
        return organize.execute(self.plan["items"], moves)

    def _run(self, opts):
        t0 = time.time()
        try:
            roots = [d for d in opts.get("drives", []) if os.path.isdir(d)]
            res = scanner.scan(roots, lambda m: self._set(message=m), self.stop)
            dup_groups = []
            if opts.get("dupes", True) and not self.stop.is_set():
                self._set(message="比对重复文件…", progress=0.0)
                dup_groups = dupes.find_duplicates(res.files, lambda m, p: self._set(message=m, progress=p), self.stop)
            judge = None
            if self.judge and opts.get("use_laya", True) and not self.stop.is_set():
                if not self.judge.ready.is_set():
                    self._set(message="等待 Laya 模型加载完成…", progress=0.0)
                    self.judge.ready.wait()
                judge = self.judge if self.judge.status == "ready" else None
            self._set(message="生成清理建议…", progress=0.0)
            items, dup_out = decide.build_items(res, dup_groups, judge, opts,
                                                lambda m, p: self._set(message=m, progress=p), self.stop)
            results = {
                "finished_at": time.time(), "seconds": round(time.time() - t0), "drives": roots,
                "stopped": self.stop.is_set(), "used_laya": judge is not None,
                "stats": {"files": res.n_files, "bytes": res.n_bytes, "errors": res.n_errors,
                          "programs": res.n_programs},
                "items": items, "dupes": dup_out,
            }
            os.makedirs(os.path.dirname(C.RESULTS_FILE), exist_ok=True)
            with open(C.RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False)
            self.results = results
            self._set(running=False, phase="done", progress=1.0,
                      message="已停止，显示已完成部分" if self.stop.is_set() else "扫描完成")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(running=False, phase="error", error=f"{type(e).__name__}: {e}", message="扫描出错")

    def _item(self, iid):
        items = (self.results or {}).get("items", [])
        return items[iid] if isinstance(iid, int) and 0 <= iid < len(items) else None

    def demo_undo(self, hid):
        for h in self.demo_history:
            if h["id"] == hid and not h["undone"]:
                h["undone"] = True
                for iid in h["ids"]:
                    self.plan["items"][iid].pop("moved_to", None)
                return {"restored": h["count"], "failed": [], "already": False, "demo": True}
        return {"restored": 0, "failed": [], "already": True, "demo": True}

    def trash(self, ids):
        if self.demo:
            done = [i for i in ids if self._item(i)]
            for i in done:
                self._item(i)["trashed"] = True
            return {"done": done, "failed": [], "demo": True}
        from send2trash import send2trash
        done, failed = [], []
        chosen = [it for it in (self._item(i) for i in ids) if it and not it.get("trashed")]
        # 先删重复副本（此时保留的那份一定还在），再删其他
        chosen.sort(key=lambda it: it["kind"] != "dupe")
        for it in chosen:
            try:
                if it["kind"] == "dupe" and not os.path.exists(it["dup_of"]):
                    raise RuntimeError("保留的那份已经不在了，为安全起见不删除这份")
                paths = it.get("paths") or [it["path"]]
                n_ok = 0
                last_err = None
                for p in paths:
                    if C.is_protected(norm(p)):
                        raise RuntimeError("位于受保护目录")
                    if not os.path.exists(p):
                        continue
                    if it["kind"] != "group":
                        st = os.stat(p)
                        if st.st_size != it["size"] or int(st.st_mtime) != int(it["mtime"]):
                            raise RuntimeError("文件在扫描后被修改过，请重新扫描")
                    try:
                        send2trash(p)
                        n_ok += 1
                    except OSError as e:
                        last_err = e
                if last_err and n_ok == 0:
                    raise last_err
                it["trashed"] = True
                done.append(it["id"])
            except Exception as e:  # noqa: BLE001
                failed.append({"id": it["id"], "error": str(e)})
        if done:
            with open(C.RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False)
        return {"done": done, "failed": failed}

    def reveal(self, iid, organize_plan=False):
        if self.demo:
            return False
        if organize_plan:
            items = (self.plan or {}).get("items", [])
            it = items[iid] if 0 <= iid < len(items) else None
        else:
            it = self._item(iid)
        if not it:
            return False
        p = it.get("moved_to") or it["path"]
        if os.path.isdir(p):
            subprocess.Popen(["explorer", p])
        elif os.path.exists(p):
            subprocess.Popen(["explorer", "/select,", p])
        else:
            return False
        return True


DEMO_DRIVES = [
    {"root": "C:\\", "total": 476 << 30, "used": 301 << 30, "free": 175 << 30},
    {"root": "D:\\", "total": 931 << 30, "used": 522 << 30, "free": 409 << 30},
]
_pick_lock = threading.Lock()


def pick_folder(title):
    """在本机弹出系统的选择文件夹对话框（服务和浏览器在同一台电脑上）。"""
    with _pick_lock:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askdirectory(parent=root, title=title, mustexist=True)
        finally:
            root.destroy()
        return os.path.normpath(path) if path else None


def list_drives():
    out = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.isdir(root):
            try:
                u = shutil.disk_usage(root)
                out.append({"root": root, "total": u.total, "used": u.used, "free": u.free})
            except OSError:
                pass
    return out


def make_handler(app, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self):
            # 防止其他网页借浏览器访问本服务
            if self.headers.get("Host") not in allowed_hosts:
                self._send(403, {"error": "bad host"})
                return False
            if self.path.startswith("/api/") and self.headers.get("X-Token") != TOKEN:
                self._send(403, {"error": "bad token"})
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            if self.path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
                    html = f.read().replace("__TOKEN__", TOKEN)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                with app.lock:
                    job = dict(app.job)
                drives = DEMO_DRIVES if app.demo else list_drives()
                self._send(200, {"model": app.model_info(), "job": job, "drives": drives,
                                 "has_results": app.results is not None, "has_plan": app.plan is not None})
            elif self.path == "/api/results":
                self._send(200, app.results or {})
            elif self.path == "/api/organize/plan":
                self._send(200, app.plan or {})
            elif self.path == "/api/organize/history":
                hist = [{k: v for k, v in h.items() if k != "ids"} for h in app.demo_history] if app.demo \
                    else organize.list_history()
                self._send(200, {"history": hist})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard():
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._send(415, {"error": "json only"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                self._send(400, {"error": "bad json"})
                return
            if self.path == "/api/scan":
                self._send(200, {"ok": app.start_scan(body)})
            elif self.path == "/api/stop":
                app.stop.set()
                self._send(200, {"ok": True})
            elif self.path == "/api/trash":
                self._send(200, app.trash([int(i) for i in body.get("ids", [])]))
            elif self.path == "/api/reveal":
                self._send(200, {"ok": app.reveal(int(body.get("id", -1)))})
            elif self.path == "/api/pick":
                self._send(200, {"path": pick_folder(body.get("title") or "选择文件夹")})
            elif self.path == "/api/organize/reveal":
                self._send(200, {"ok": app.reveal(int(body.get("id", -1)), organize_plan=True)})
            elif self.path == "/api/organize/start":
                self._send(200, {"ok": app.start_organize(body)})
            elif self.path == "/api/organize/run":
                moves = [{"id": int(m["id"]), "dest": str(m["dest"])} for m in body.get("moves", [])]
                self._send(200, app.run_organize(moves))
            elif self.path == "/api/organize/undo" and app.demo:
                self._send(200, app.demo_undo(str(body.get("id", ""))))
            elif self.path == "/api/organize/undo":
                try:
                    res = organize.undo(str(body.get("id", "")))
                    back = set(res["restored_paths"])
                    for it in (app.plan or {}).get("items", []):
                        if it.get("moved_to") and it["path"] in back:
                            it.pop("moved_to")
                    self._send(200, res)
                except (OSError, ValueError) as e:
                    self._send(400, {"error": str(e)})
            else:
                self._send(404, {"error": "not found"})

    return H


def find_running(first_port):
    """已经有本工具在运行的话返回它的地址。"""
    import urllib.request
    for port in range(first_port, first_port + 20):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url, timeout=0.3) as r:
                if "<title>盘算 Reckon</title>" in r.read(2048).decode("utf-8", "ignore"):
                    return url
        except OSError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-laya", action="store_true", help="不加载模型，只用规则")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo", action="store_true", help="演示模式：只展示 docs/demo 里的示例数据，不扫描、不改动文件")
    ap.add_argument("--organize", nargs="*", metavar="PATH",
                    help="打开整理页面并填入这些文件/文件夹（拖到 整理.bat 上或右键发送到时使用）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    fragment = ""
    if args.organize is not None:
        from urllib.parse import quote
        fragment = "#organize=" + quote(json.dumps([os.path.abspath(p) for p in args.organize], ensure_ascii=False))
        running = find_running(args.port)
        if running:
            import webbrowser
            webbrowser.open(running + fragment)
            print("已在运行中的窗口打开整理页面：" + running)
            return

    app = App(use_laya=not args.no_laya, demo=args.demo)
    port = args.port
    for port in range(args.port, args.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
            break
        except OSError:
            continue
    else:
        sys.exit("找不到可用端口")
    url = f"http://127.0.0.1:{port}/"
    print(f"盘算 Reckon 已启动：{url}")
    print("Laya 模型在后台加载（约 1 分钟），可以先设置扫描选项。关闭此窗口即退出。")
    if not args.no_browser:
        import webbrowser
        webbrowser.open(url + fragment)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
