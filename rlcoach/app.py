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

from . import shortcut, statsapi
from .library import Library, data_dir
from .live.server import LiveServer
from .update import Updater

UI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)) / "rlcoach" / "ui"
if not UI_DIR.is_dir():
    UI_DIR = Path(__file__).parent / "ui"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

LIB: Library | None = None
WINDOW = None  # the pywebview window, when there is one
STATS: statsapi.StatsClient | None = None
LIVE: LiveServer | None = None
UPDATER: Updater | None = None
DEFAULT_PORT = 49209  # fixed so the BakkesMod plugin can tell the app is already open
PLUGIN_DLL = UI_DIR.parent / "plugin" / "RLCoachLive.dll"
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
            return self._json(UPDATER.status())
        if url.path == "/api/shortcut":
            return self._json(shortcut_status(lib))
        if url.path == "/api/live":
            return self._json(live_status(lib))
        if url.path == "/api/live/history":
            return self._json({"attempts": LIVE.history()})
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
            if "auto_update" in body:
                lib.settings["auto_update"] = UPDATER.enabled = bool(body["auto_update"])
            lib.save_settings()
            return self._json({"ok": True, "you": lib.you(), "folders": lib.settings["folders"],
                               "auto_update": lib.settings.get("auto_update", True)})
        if url.path == "/api/shortcut":
            res = shortcut.create() if body.get("create") else shortcut.remove()
            lib.settings["shortcut_offered"] = True
            lib.save_settings()
            return self._json({**res, **shortcut_status(lib)})
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
        if url.path == "/api/update/check":
            threading.Thread(target=UPDATER.check_and_download, daemon=True).start()
            return self._json({"ok": True})
        if url.path == "/api/update/apply":
            ok = UPDATER.apply_and_restart()
            if ok:  # answer the page first, then get out of the updater's way
                threading.Timer(0.8, lambda: os._exit(0)).start()
            return self._json({"ok": ok})
        if url.path == "/api/live/enable-stats":
            install = statsapi.find_install(lib.settings.get("rl_install"))
            if install is None:
                return self._json({"ok": False, "error": "Could not find Rocket League. Set its folder in Settings."})
            try:
                cfg = statsapi.enable(install)
            except OSError as e:
                return self._json({"ok": False, "error": f"Could not write the settings file: {e}"})
            return self._json({"ok": True, "settings": cfg})
        if url.path == "/api/live/seek":
            data = {"Frame": int(body["frame"])} if body.get("frame") is not None else {"TimeSeconds": float(body.get("time", 0))}
            return self._json({"ok": STATS.send("SeekReplay", data)})
        if url.path == "/api/live/load-replay":
            p = lib.paths.get(body.get("id"))
            return self._json({"ok": bool(p) and STATS.send("LoadReplay", {"Path": str(p)})})
        if url.path == "/api/live/speed":
            return self._json({"ok": STATS.send("SetGameSpeed", {"Speed": float(body.get("speed", 1))})})
        if url.path == "/api/live/pause":
            return self._json({"ok": STATS.send("SetMatchPaused", {"bPaused": bool(body.get("paused"))})})
        if url.path == "/api/live/focus":
            LIVE.focus = body.get("kind") or None
            LIVE.tip(f"Now practising: {LIVE.focus.replace('_', ' ') if LIVE.focus else 'everything'}.")
            return self._json({"ok": True})
        if url.path == "/api/live/say":
            LIVE.tip(str(body.get("text", ""))[:200], 4.0)
            return self._json({"ok": True})
        if url.path == "/api/live/install-plugin":
            return self._json(install_plugin())
        if url.path == "/api/settings/rl-install":
            lib.settings["rl_install"] = (body.get("path") or "").strip() or None
            lib.save_settings()
            return self._json(live_status(lib))
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


def _bakkesmod_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "bakkesmod" / "bakkesmod"


def install_plugin() -> dict:
    """Copy the bundled plugin into BakkesMod and make it load automatically."""
    bm = _bakkesmod_dir()
    if not bm.is_dir():
        return {"ok": False, "error": "BakkesMod is not installed. Get it from bakkesmod.com, run it once, then try again."}
    if not PLUGIN_DLL.is_file():
        return {"ok": False, "error": "This build of RL Coach does not include the plugin. Download RLCoach.exe from GitHub Releases."}
    (bm / "plugins").mkdir(exist_ok=True)
    shutil.copy2(PLUGIN_DLL, bm / "plugins" / "RLCoachLive.dll")
    cfg = bm / "cfg" / "plugins.cfg"
    cfg.parent.mkdir(exist_ok=True)
    text = cfg.read_text(encoding="utf-8", errors="ignore") if cfg.exists() else ""
    if "plugin load rlcoachlive" not in text.lower():
        cfg.write_text(text.rstrip("\n") + "\nplugin load rlcoachlive\n", encoding="utf-8")
    return {"ok": True, "path": str(bm / "plugins" / "RLCoachLive.dll")}


def sync_plugin() -> None:
    """After an update, refresh the installed BakkesMod plugin from the copy bundled in this version."""
    target = _bakkesmod_dir() / "plugins" / "RLCoachLive.dll"
    if not (target.is_file() and PLUGIN_DLL.is_file()):
        return
    try:
        if target.read_bytes() != PLUGIN_DLL.read_bytes():
            shutil.copy2(PLUGIN_DLL, target)
    except OSError:
        pass  # the game has it loaded right now; we will try again next start


def plugin_installed() -> bool:
    return (_bakkesmod_dir() / "plugins" / "RLCoachLive.dll").is_file()


def shortcut_status(lib: Library) -> dict:
    """Whether there is a desktop shortcut, and whether to ask about one (only once, on first run)."""
    if not shortcut.SUPPORTED:
        return {"supported": False, "exists": False, "offer": False}
    has = shortcut.exists()
    return {"supported": True, "exists": has, "offer": not has and not lib.settings.get("shortcut_offered")}


def live_status(lib: Library) -> dict:
    install = statsapi.find_install(lib.settings.get("rl_install"))
    stats = STATS.snapshot()
    stats["install"] = str(install) if install else None
    stats["settings"] = statsapi.read_settings(install) if install else None
    stats["replay_id"] = lib.match_replay(stats.get("replay_file"), stats.get("players") or []) if stats["in_replay"] else None
    return {
        "stats": stats,
        "plugin": {**LIVE.snapshot(), "installed": plugin_installed(), "bakkesmod": _bakkesmod_dir().is_dir(),
                   "bundled": PLUGIN_DLL.is_file()},
    }


def _save_launch_command() -> None:
    """Remember how to start this app so the BakkesMod plugin can open it."""
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        exe = Path(sys.executable)
        w = exe.with_name("pythonw.exe")
        cmd = f'"{w if w.exists() else exe}" -m rlcoach.app'
    try:
        (data_dir() / "launch.txt").write_text(cmd, encoding="utf-8")
    except OSError:
        pass


def _already_running(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


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
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--live", action="store_true", help="open on the Live tab (used by the BakkesMod plugin)")
    ap.add_argument("--browser", action="store_true", help="open in your browser instead of an app window")
    ap.add_argument("--no-window", action="store_true", help="only run the server (for testing)")
    args = ap.parse_args(argv)

    global STATS, LIVE, UPDATER
    if _already_running(args.port):  # the app is already open: just show it
        webbrowser.open(f"http://127.0.0.1:{args.port}/" + ("#live" if args.live else ""))
        return 0
    LIB = Library()
    UPDATER = Updater(data_dir(), enabled=LIB.settings.get("auto_update", True))
    if UPDATER.pending_on_start() is not None:  # a verified update was downloaded last time: install it first
        relaunch = (["--live"] if args.live else []) + (["--browser"] if args.browser else [])
        if UPDATER.apply_and_restart(relaunch):
            return 0
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    UPDATER.cleanup()
    UPDATER.start()
    sync_plugin()
    threading.Thread(target=shortcut.repair, daemon=True).start()
    STATS = statsapi.StatsClient()
    LIVE = LiveServer(data_dir())
    _save_launch_command()
    url = f"http://127.0.0.1:{server.server_address[1]}/" + ("#live" if args.live else "")
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
