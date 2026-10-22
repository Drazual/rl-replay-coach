"""The replay library: which replays exist, their header info, and cached analyses."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import threading
import traceback
from collections import Counter
from pathlib import Path

import subtr_actor

from .cli import default_replay_dir
from .names import map_name, playlist_name


def data_dir() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    d = Path(base) / "rlcoach" if base else Path.home() / ".rlcoach"
    (d / "cache").mkdir(parents=True, exist_ok=True)
    return d


def replay_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]


def _stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


class Library:
    def __init__(self):
        self.dir = data_dir()
        self.settings_path = self.dir / "settings.json"
        self.index_path = self.dir / "index.json"
        self.settings = self._read(self.settings_path, {"folders": [], "player": None, "labels": {}})
        if not self.settings["folders"]:
            d = default_replay_dir()
            if d:
                self.settings["folders"].append(str(d))
        self.index: dict = self._read(self.index_path, {})
        self.paths: dict[str, Path] = {}
        self.status: dict[str, str] = {}
        self.lock = threading.Lock()
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- storage
    @staticmethod
    def _read(path: Path, default):
        try:
            return {**default, **json.loads(path.read_text(encoding="utf-8"))} if isinstance(default, dict) else default
        except (OSError, ValueError):
            return default

    def _write(self, path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    def save_settings(self) -> None:
        self._write(self.settings_path, self.settings)

    # ---------------------------------------------------------------- listing
    def _header(self, path: Path) -> dict:
        meta = subtr_actor.get_replay_meta(str(path))["replay_meta"]
        h = dict(meta.get("all_headers", []))
        players = []
        for team, side in ((0, "team_zero"), (1, "team_one")):
            stats = {p.get("Name"): p for p in h.get("PlayerStats", []) or []}
            for p in meta.get(side, []) or []:
                players.append({"name": p.get("name"), "team": team, "bot": bool(stats.get(p.get("name"), {}).get("bBot"))})
        fps = h.get("RecordFPS") or 30
        return {
            "title": h.get("ReplayName") or "",
            "date": str(h.get("Date", "")),
            "map": map_name(str(h.get("MapName", ""))),
            "playlist": playlist_name(meta),
            "team_size": h.get("TeamSize") or max(1, len(players) // 2),
            "score": [h.get("Team0Score", 0) or 0, h.get("Team1Score", 0) or 0],
            "players": players,
            "recorder": h.get("PlayerName"),
            "length": round((h.get("NumFrames") or 0) / fps),
        }

    def scan(self) -> list[dict]:
        """List every replay in the configured folders, newest first."""
        found = []
        for folder in self.settings["folders"]:
            p = Path(folder)
            if p.is_dir():
                found += list(p.glob("*.replay"))
        changed = False
        rows = []
        for path in found:
            rid = replay_id(path)
            self.paths[rid] = path
            stamp = _stamp(path)
            entry = self.index.get(rid)
            if not entry or entry.get("stamp") != stamp:
                try:
                    entry = {"stamp": stamp, **self._header(path)}
                except Exception as e:  # unreadable or very old replay
                    entry = {"stamp": stamp, "error": str(e)[:200], "title": "", "date": "", "players": []}
                self.index[rid] = entry
                changed = True
            rows.append({
                "id": rid,
                "file": path.name,
                "label": self.settings["labels"].get(rid, ""),
                "mtime": path.stat().st_mtime,
                "analysed": self.cache_path(rid, stamp).exists(),
                "status": self.status.get(rid),
                **{k: v for k, v in entry.items() if k != "stamp"},
            })
        if changed:
            self._write(self.index_path, self.index)
        rows.sort(key=lambda r: (r.get("date") or "", r["mtime"]), reverse=True)
        return rows

    def you(self) -> str | None:
        """The user's in-game name: set in settings, else whoever saved most replays."""
        if self.settings.get("player"):
            return self.settings["player"]
        names = Counter(e.get("recorder") for e in self.index.values() if e.get("recorder"))
        return names.most_common(1)[0][0] if names else None

    # ---------------------------------------------------------------- analysis
    def cache_path(self, rid: str, stamp: str | None = None) -> Path:
        from .analysis import ANALYSIS_VERSION

        if stamp is None:
            stamp = _stamp(self.paths[rid])
        return self.dir / "cache" / f"{rid}-{stamp}-v{ANALYSIS_VERSION}.json"

    def get_analysis(self, rid: str) -> dict:
        if rid not in self.paths:
            self.scan()
        if rid not in self.paths:
            return {"status": "missing"}
        cp = self.cache_path(rid)
        if cp.exists():
            data = json.loads(cp.read_text(encoding="utf-8"))
            data["status"] = "done"
            return data
        st = self.status.get(rid, "")
        if st.startswith("error"):
            return {"status": st}
        if st not in ("queued", "running"):
            self.enqueue(rid, priority=True)
        return {"status": self.status.get(rid, "queued")}

    def enqueue(self, rid: str, priority: bool = False) -> None:
        with self.lock:
            if self.status.get(rid) in ("queued", "running"):
                if priority:
                    self.priority = rid
                return
            self.status[rid] = "queued"
        if priority:
            self.priority = rid
        self.jobs.put(rid)

    priority: str | None = None

    def _worker(self) -> None:
        from .analysis import analyse

        pending: list[str] = []
        while True:
            if not pending:
                pending.append(self.jobs.get())
            while not self.jobs.empty():
                pending.append(self.jobs.get_nowait())
            # Whatever the user is looking at right now goes first.
            if self.priority in pending:
                pending.remove(self.priority)
                pending.insert(0, self.priority)
                self.priority = None
            rid = pending.pop(0)
            path = self.paths.get(rid)
            if path is None or self.cache_path(rid).exists():
                self.status.pop(rid, None)
                continue
            self.status[rid] = "running"
            try:
                data = analyse(str(path), self.you())
                self._write(self.cache_path(rid), data)
                self.status.pop(rid, None)
            except Exception as e:
                traceback.print_exc()
                self.status[rid] = f"error: {e}"[:300]

    def analyse_all(self) -> int:
        n = 0
        for row in self.scan():
            if not row["analysed"] and not row.get("error"):
                self.enqueue(row["id"])
                n += 1
        return n

    # ---------------------------------------------------------------- trends
    def trends(self) -> dict:
        """The user's numbers from every analysed replay, oldest first."""
        you = (self.you() or "").lower()
        rows = []
        listing = self.scan()
        for row in listing:
            if not row["analysed"]:
                continue
            try:
                data = json.loads(self.cache_path(row["id"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            me = next((p for p in data["players"] if p["name"].lower() == you), None)
            if me is None:
                continue
            rep = data["reports"].get(me["key"], {})
            rows.append({
                "id": row["id"],
                "date": data["date"],
                "map": data["map"],
                "playlist": data["playlist"],
                "won": rep.get("won"),
                "metrics": data["metrics"].get(me["key"], {}),
                "findings": [f["key"] for f in rep.get("findings", [])],
                "finding_titles": {f["key"]: f["title"] for f in rep.get("findings", [])},
            })
        rows.sort(key=lambda r: r["date"])
        total = sum(1 for r in listing if not r.get("error"))
        unanalysed = sum(1 for r in listing if not r["analysed"] and not r.get("error"))
        return {"you": self.you(), "games": rows, "total": total, "unanalysed": unanalysed}
