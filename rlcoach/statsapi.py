"""Connection to Rocket League's official Stats API.

The Stats API is built into the game (no mods, works with Easy Anti-Cheat on).
It is off by default; turning it on means setting PacketSendRate in
<install>/TAGame/Config/TAStatsAPI.ini before the game starts. Once on, the
game streams JSON over a local WebSocket and accepts a few commands, including
LoadReplay and SeekReplay, which is what lets the app follow along while you
watch a replay and jump the replay to a moment you click.

Docs: https://www.rocketleague.com/en/developer/stats-api
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from collections import deque
from pathlib import Path

SECTION = "[TAGame.MatchStatsExporter_TA]"
DEFAULT_WEB_PORT = 49124


# ------------------------------------------------------------------ finding the game


def _steam_libraries() -> list[Path]:
    roots = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
        roots += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    else:
        roots += [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"]
    libs = []
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
        libs.append(root)
    return libs


def _epic_installs() -> list[Path]:
    manifests = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    out = []
    if manifests.is_dir():
        for item in manifests.glob("*.item"):
            try:
                data = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
            except ValueError:
                continue
            if "rocket league" in str(data.get("DisplayName", "")).lower():
                out.append(Path(data.get("InstallLocation", "")))
    return out


def find_install(override: str | None = None) -> Path | None:
    """The Rocket League install folder (the one that contains TAGame)."""
    candidates = [Path(override)] if override else []
    candidates += _epic_installs()
    candidates += [lib / "steamapps" / "common" / "rocketleague" for lib in _steam_libraries()]
    for c in candidates:
        if (c / "TAGame" / "Config").is_dir():
            return c
    return None


# ------------------------------------------------------------------ the ini setting


def ini_path(install: Path) -> Path:
    cfg = install / "TAGame" / "Config"
    ta = cfg / "TAStatsAPI.ini"
    if ta.exists() or not (cfg / "DefaultStatsAPI.ini").exists():
        return ta
    return cfg / "DefaultStatsAPI.ini"


def read_settings(install: Path) -> dict:
    p = ini_path(install)
    out = {"file": str(p), "PacketSendRate": 0.0, "Port": 49123, "WebPort": DEFAULT_WEB_PORT}
    if not p.exists():
        return out
    in_section = False
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_section = s.lower() == SECTION.lower()
        elif in_section and "=" in s:
            k, v = (x.strip() for x in s.split("=", 1))
            if k in out:
                try:
                    out[k] = float(v) if k == "PacketSendRate" else int(v)
                except ValueError:
                    pass
    return out


def enable(install: Path, rate: int = 30) -> dict:
    """Turn the Stats API on. Keeps a backup of the original file next to it."""
    p = ini_path(install)
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines() if p.exists() else []
    if p.exists() and not p.with_suffix(".ini.rlcoach-backup").exists():
        shutil.copy2(p, p.with_suffix(".ini.rlcoach-backup"))
    want = {"PacketSendRate": str(rate), "Port": "49123", "WebPort": str(DEFAULT_WEB_PORT)}

    out, in_section, seen_section, done = [], False, False, set()
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            if in_section:  # leaving our section: add anything missing
                out += [f"{k}={v}" for k, v in want.items() if k not in done]
            in_section = s.lower() == SECTION.lower()
            seen_section = seen_section or in_section
        elif in_section and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in want:
                out.append(f"{k}={want[k]}")
                done.add(k)
                continue
        out.append(line)
    if in_section:
        out += [f"{k}={v}" for k, v in want.items() if k not in done]
    if not seen_section:
        out += ["", SECTION] + [f"{k}={v}" for k, v in want.items()]
    p.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    return read_settings(install)


# ------------------------------------------------------------------ live client


class StatsClient:
    """Keeps a WebSocket open to the game and remembers the latest state."""

    def __init__(self, port: int = DEFAULT_WEB_PORT):
        self.port = port
        self.ws = None
        self.connected = False
        self.state: dict = {}  # last UpdateState Data
        self.replay_file: str | None = None
        self.events: deque = deque(maxlen=50)
        self.last_update = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            import websocket  # websocket-client
        except ImportError:
            return
        while True:
            try:
                ws = websocket.create_connection(f"ws://127.0.0.1:{self.port}", timeout=5)
                self.ws, self.connected = ws, True
                ws.settimeout(10)
                while True:
                    raw = ws.recv()
                    if not raw:
                        break
                    for msg in _split_json(raw):
                        self._handle(msg)
            except Exception:
                pass
            self.ws, self.connected = None, False
            time.sleep(3)  # game not running or API off; try again shortly

    def _handle(self, msg: dict):
        event, data = msg.get("Event"), msg.get("Data") or {}
        if isinstance(data, str):  # tolerate a JSON-encoded string payload
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        with self.lock:
            if event == "UpdateState":
                self.state = data
                self.last_update = time.time()
            elif event == "ReplayCreated":
                self.replay_file = data.get("FileName")
            elif event in ("MatchDestroyed",):
                self.replay_file = None
                self.state = {}
            if event != "UpdateState":
                self.events.append({"event": event, "data": data, "at": time.time()})

    def send(self, command: str, data: dict) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps({"Command": command, "Data": data}))
            return True
        except Exception:
            return False

    def snapshot(self) -> dict:
        with self.lock:
            game = self.state.get("Game", {}) or {}
            return {
                "connected": self.connected,
                "fresh": time.time() - self.last_update < 3,
                "in_replay": bool(game.get("bReplay")),
                "frame": game.get("Frame"),
                "elapsed": game.get("Elapsed"),
                "clock": game.get("TimeSeconds"),
                "arena": game.get("Arena"),
                "replay_file": self.replay_file,
                "players": [p.get("Name") for p in self.state.get("Players", []) or []],
                "target": (game.get("Target") or {}).get("Name") if game.get("bHasTarget") else None,
                "events": list(self.events)[-10:],
            }


def _split_json(raw) -> list[dict]:
    """One frame may hold one message or several back to back."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    out, dec, i = [], json.JSONDecoder(), 0
    raw = raw.strip()
    while i < len(raw):
        try:
            obj, j = dec.raw_decode(raw, i)
        except ValueError:
            break
        if isinstance(obj, dict):
            out.append(obj)
        i = j
        while i < len(raw) and raw[i] in " \r\n\t":
            i += 1
    return out
