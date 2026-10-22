"""The desktop app: a small local web server plus a native window around it.

Double-click "RL Coach" (or run `rlcoach-app`). The window is a pywebview
window when pywebview is installed; otherwise the app opens in Edge or Chrome
"app mode", and as a last resort in a normal browser tab. Everything runs on
127.0.0.1 and only reads replay files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .library import Library

UI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)) / "rlcoach" / "ui"
if not UI_DIR.is_dir():
    UI_DIR = Path(__file__).parent / "ui"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

LIB: Library | None = None
WINDOW = None  # the pywebview window, when there is one
LAST_PING: float | None = None  # when the UI last checked in; used to quit after the window closes


def pick_folder() -> str | None:
    if WINDOW is not None:
        import webview

        res = WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None
    # Run tkinter in its own process so it never fights the server threads.
    code = ("import tkinter as tk, tkinter.filedialog as fd; r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True);"
            "print(fd.askdirectory(title='Choose a replay folder') or '')")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
        return out.stdout.strip() or None
    except Exception:
        return None


def reveal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        lib = LIB
        if url.path == "/api/replays":
            return self._json({"replays": lib.scan(), "you": lib.you(), "folders": lib.settings["folders"]})
        if url.path == "/api/replay":
            return self._json(lib.get_analysis(q.get("id", "")))
        if url.path == "/api/trends":
            return self._json(lib.trends())
        if url.path == "/api/update":
            from .update import check

            return self._json(check())
        if url.path == "/api/status":
            global LAST_PING
            LAST_PING = time.time()
            return self._json({"status": dict(lib.status)})
        return self._static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        lib = LIB
        body = self._body()
        if url.path == "/api/settings":
            if "player" in body:
                lib.settings["player"] = (body["player"] or "").strip() or None
            if "folders" in body:
                lib.settings["folders"] = [f for f in body["folders"] if f]
            lib.save_settings()
            return self._json({"ok": True, "you": lib.you(), "folders": lib.settings["folders"]})
        if url.path == "/api/pick-folder":
            folder = pick_folder()
            if folder and folder not in lib.settings["folders"]:
                lib.settings["folders"].append(folder)
                lib.save_settings()
            return self._json({"folder": folder, "folders": lib.settings["folders"]})
        if url.path == "/api/label":
            rid, label = body.get("id"), (body.get("label") or "").strip()
            if label:
                lib.settings["labels"][rid] = label
            else:
                lib.settings["labels"].pop(rid, None)
            lib.save_settings()
            return self._json({"ok": True})
        if url.path == "/api/analyse-all":
            return self._json({"queued": lib.analyse_all()})
        if url.path == "/api/reveal":
            p = lib.paths.get(body.get("id"))
            if p:
                reveal(p)
            return self._json({"ok": bool(p)})
        if url.path == "/api/open-url":
            link = body.get("url", "")
            if link.startswith("https://github.com/"):
                webbrowser.open(link)
            return self._json({"ok": True})
        if url.path == "/api/retry":
            rid = body.get("id")
            lib.status.pop(rid, None)
            return self._json(lib.get_analysis(rid))
        return self._json({"error": "not found"}, 404)

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (UI_DIR / rel).resolve()
        if UI_DIR.resolve() not in f.parents or not f.is_file():
            self.send_error(404)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _app_mode_browser(url: str) -> bool:
    """Open the UI in a chromeless Edge/Chrome window if one is installed."""
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                               Path(base) / "Google/Chrome/Application/chrome.exe"]
    else:
        candidates += [Path(p) for p in filter(None, (shutil.which(n) for n in
                       ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")))]
    for exe in candidates:
        if exe.is_file():
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1400,900"])
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    global LIB, WINDOW
    import argparse

    ap = argparse.ArgumentParser(prog="rlcoach-app", description="Rocket League replay coach")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="open in your browser instead of an app window")
    ap.add_argument("--no-window", action="store_true", help="only run the server (for testing)")
    args = ap.parse_args(argv)

    LIB = Library()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"RL Coach running at {url}", flush=True)

    if args.no_window:
        server.serve_forever()
        return 0

    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not args.browser:
        try:
            import webview

            WINDOW = webview.create_window("RL Coach", url, width=1400, height=900, min_size=(900, 600))
            webview.start()
            return 0
        except Exception:
            WINDOW = None
    if args.browser or not _app_mode_browser(url):
        webbrowser.open(url)
    # Without a native window we cannot see it close, so quit once the page
    # has stopped checking in for a while (it pings every few seconds).
    try:
        while True:
            time.sleep(5)
            if LAST_PING is not None and time.time() - LAST_PING > 45:
                return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
