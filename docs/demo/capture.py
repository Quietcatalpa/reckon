"""生成 README 用的截图和演示动图：启动演示模式 + 无头 Edge/Chrome，按脚本操作页面。

    python docs/demo/capture.py     # 输出 docs/images/ 下的 4 张截图（清理/整理 × 浅色/深色）和 demo.gif

依赖：websocket-client、Pillow；本机装有 Edge 或 Chrome（可用环境变量 BROWSER 指定路径）。
全程用的是演示模式的虚构数据，不会读写你的真实文件。
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(os.path.dirname(HERE))
IMAGES = os.path.join(TOOL_DIR, "docs", "images")
OUT = os.path.join(IMAGES, "demo.gif")
PORT, DEBUG_PORT = 8799, 9333
VIEW_W, VIEW_H, OUT_W = 1280, 860, 960
FPS = 8

BROWSERS = [
    os.environ.get("BROWSER", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# 页面里加一个假鼠标和点击波纹
CURSOR_JS = r"""
(() => {
  if (window.__cur) return true;
  const c = document.createElement('div');
  c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M4 2l16 10-7 1.6L9.6 21z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  Object.assign(c.style, {position: 'fixed', left: '900px', top: '420px', zIndex: 99999, pointerEvents: 'none',
    transition: 'left .7s ease-in-out, top .7s ease-in-out', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.35))'});
  const ring = document.createElement('div');
  Object.assign(ring.style, {position: 'fixed', width: '36px', height: '36px', marginLeft: '-18px', marginTop: '-18px',
    borderRadius: '50%', border: '3px solid #c96442', opacity: 0, zIndex: 99998, pointerEvents: 'none'});
  document.body.append(c, ring);
  const el = (s) => typeof s === 'string' ? document.querySelector(s) : s;
  const center = (e) => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  window.__cur = {
    move(s) { const [x, y] = center(el(s)); c.style.left = (x - 4) + 'px'; c.style.top = (y - 2) + 'px'; return true; },
    click(s) {
      const e = el(s), [x, y] = center(e);
      ring.style.transition = 'none'; ring.style.left = x + 'px'; ring.style.top = y + 'px';
      ring.style.transform = 'scale(.4)'; ring.style.opacity = .9; ring.offsetWidth;
      ring.style.transition = 'transform .45s ease-out, opacity .45s ease-out';
      ring.style.transform = 'scale(1.5)'; ring.style.opacity = 0;
      e.click(); return true;
    },
    choose(s, text) {
      const e = el(s), o = [...e.options].find((o) => o.textContent.includes(text));
      e.value = o.value; e.dispatchEvent(new Event('change', {bubbles: true})); return true;
    },
    scrollTo(s, offset) {
      const y = el(s).getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({top: y, behavior: 'smooth'}); return true;
    },
  };
  return true;
})()
"""

# (要执行的 JS, 之后录多少秒)
TIMELINE = [
    ("true", 1.2),
    ("__cur.scrollTo('#explain', 16)", 1.4),
    ("__cur.move('.tile[data-tab=dupes]')", 0.8),
    ("__cur.click('.tile[data-tab=dupes]')", 1.4),
    ("__cur.move(document.querySelectorAll('#list .dg input')[0])", 0.8),
    ("__cur.click(document.querySelectorAll('#list .dg input')[0])", 0.4),
    ("__cur.move(document.querySelectorAll('#list .dg input')[1])", 0.5),
    ("__cur.click(document.querySelectorAll('#list .dg input')[1])", 0.9),
    ("__cur.move('.tile[data-tab=delete]')", 0.8),
    ("__cur.click('.tile[data-tab=delete]')", 1.2),
    ("__cur.move(document.querySelectorAll('#list .it input')[0])", 0.8),
    ("__cur.click(document.querySelectorAll('#list .it input')[0])", 0.5),
    ("__cur.move('#trash')", 0.9),
    ("__cur.click('#trash')", 1.4),
    ("__cur.move('#askOk')", 0.7),
    ("__cur.click('#askOk')", 2.4),
    ("__cur.move('#modes [data-mode=org]')", 0.9),
    ("__cur.click('#modes [data-mode=org]')", 1.6),
    ("__cur.scrollTo('#oout', 70)", 1.3),
    ("__cur.move('#ofilter')", 0.8),
    ("__cur.click('#ofilter'); __cur.choose('#ofilter', '需要你选')", 1.3),
    ("__cur.move('#olist select')", 0.8),
    ("__cur.click('#olist select'); __cur.choose('#olist select', '07_归档')", 1.3),
    ("__cur.choose('#ofilter', '全部')", 1.0),
    ("__cur.move('#oselall')", 0.8),
    ("__cur.click('#oselall')", 1.0),
    ("__cur.move('#orun')", 0.8),
    ("__cur.click('#orun')", 1.3),
    ("__cur.move('#askOk')", 0.7),
    ("__cur.click('#askOk')", 2.2),
    ("__cur.scrollTo('#hist', 420)", 1.2),
    ("__cur.move('#hist [data-undo]')", 0.8),
    ("__cur.click('#hist [data-undo]')", 1.3),
    ("__cur.move('#askOk')", 0.7),
    ("__cur.click('#askOk')", 2.6),
]


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS 出错：{expr}\n{r['exceptionDetails']}")
        return r["result"].get("value")

    def shot(self):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


def wait_http(url, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.read()
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"等不到 {url}")


def open_page(cdp, path):
    cdp.call("Page.navigate", url="about:blank")  # 只改 #hash 不会重新加载，先跳走再回来
    time.sleep(0.2)
    cdp.call("Page.navigate", url=f"http://127.0.0.1:{PORT}{path}")
    ready = "#olist .oit" if "org" in path else "#list .it"
    for _ in range(50):  # 等列表渲染出来
        time.sleep(0.2)
        if cdp.js(f"document.querySelectorAll('{ready}').length > 0"):
            break
    time.sleep(0.5)


def screenshots(cdp):
    """清理页、整理页各拍浅色和深色一张（1280×1240 视口，1.5 倍清晰度）。"""
    cdp.call("Emulation.setDeviceMetricsOverride", width=VIEW_W, height=1240, deviceScaleFactor=1.5, mobile=False)
    for scheme in ("light", "dark"):
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])
        for name, path in (("clean", "/"), ("organize", "/#org")):
            open_page(cdp, path)
            data = cdp.call("Page.captureScreenshot", format="png")["data"]
            with open(os.path.join(IMAGES, f"{name}-{scheme}.png"), "wb") as f:
                f.write(base64.b64decode(data))
    print("已生成 4 张截图")


def record(cdp):
    frames = []  # (时间戳, 图)
    for expr, hold in TIMELINE:
        cdp.js(expr)
        end = time.time() + hold
        while time.time() < end:
            t = time.time()
            frames.append((t, cdp.shot()))
            time.sleep(max(0, 1 / FPS - (time.time() - t)))
    return frames


def to_gif(frames, out):
    h = round(VIEW_H * OUT_W / VIEW_W)
    imgs = [f.resize((OUT_W, h), Image.LANCZOS) for _, f in frames]
    # 用几帧拼出一个共享调色板，界面是平涂色，不抖动更清楚、也更小
    sample = Image.new("RGB", (OUT_W, h * 4))
    for i, k in enumerate(range(0, len(imgs), max(1, len(imgs) // 4))):
        if i < 4:
            sample.paste(imgs[k], (0, h * i))
    pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pimgs = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
    # 截图偶尔会卡（机器忙时），单帧时长封顶，避免播放时突然定住；静止画面靠下面的合并保留停留时间
    durs = [min(250, int((frames[i + 1][0] - frames[i][0]) * 1000)) for i in range(len(frames) - 1)] + [2500]
    # 合并完全相同的相邻帧
    out_imgs, out_durs = [pimgs[0]], [durs[0]]
    for im, d in zip(pimgs[1:], durs[1:]):
        if im.tobytes() == out_imgs[-1].tobytes():
            out_durs[-1] += d
        else:
            out_imgs.append(im)
            out_durs.append(d)
    out_imgs[0].save(out, save_all=True, append_images=out_imgs[1:], duration=out_durs, loop=0)
    return len(out_imgs), sum(out_durs) / 1000


def main():
    browser = next((b for b in BROWSERS if b and os.path.exists(b)), None)
    if not browser:
        sys.exit("没找到 Edge 或 Chrome，可以用环境变量 BROWSER 指定")
    profile = tempfile.mkdtemp(prefix="demo_gif_")
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    hidden = subprocess.STARTUPINFO() if os.name == "nt" else None
    if hidden:
        hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # wShowWindow 默认 0 = 隐藏
    srv = subprocess.Popen([sys.executable, "-m", "reckon", "--demo", "--no-browser", "--port", str(PORT)],
                           cwd=TOOL_DIR, creationflags=new_console, startupinfo=hidden)
    br = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                           f"--remote-debugging-port={DEBUG_PORT}", "--remote-allow-origins=*",
                           f"--user-data-dir={profile}", f"--window-size={VIEW_W},{VIEW_H}", "about:blank"])
    cdp = None
    try:
        wait_http(f"http://127.0.0.1:{PORT}/")
        targets = json.loads(wait_http(f"http://127.0.0.1:{DEBUG_PORT}/json/list"))
        page = next(t for t in targets if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        os.makedirs(IMAGES, exist_ok=True)
        screenshots(cdp)  # 先拍截图：录动图会改动演示数据（标记已删除、已整理）
        cdp.call("Emulation.setDeviceMetricsOverride", width=VIEW_W, height=VIEW_H, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        open_page(cdp, "/")
        cdp.js(CURSOR_JS)
        frames = record(cdp)
        n, secs = to_gif(frames, OUT)
        print(f"已生成 {OUT}：{n} 帧，{secs:.1f} 秒，{os.path.getsize(OUT) / 1e6:.1f} MB")
    finally:
        # Edge 的启动进程会把真正的浏览器进程分出去，只 terminate 关不干净，先通过调试协议关闭
        if cdp is not None:
            try:
                cdp.call("Browser.close")
            except Exception:  # noqa: BLE001
                pass
        br.terminate()
        srv.terminate()
        try:
            br.wait(5)
        except subprocess.TimeoutExpired:
            br.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
