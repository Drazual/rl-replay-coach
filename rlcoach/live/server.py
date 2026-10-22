"""Receives car + input frames from the RL Coach BakkesMod plugin and sends tips back.

Both directions are UDP on 127.0.0.1 only. The plugin only runs when Rocket
League is started without Easy Anti-Cheat, and it stops sending in online games.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .mechanics import MechanicsTracker, summary

FRAME_PORT = 49210  # plugin -> app
TIP_PORT = 49211  # app -> plugin


class LiveServer:
    def __init__(self, log_dir: Path):
        self.tracker = MechanicsTracker()
        self.attempts: deque = deque(maxlen=200)
        self.mode: str | None = None
        self.player: str | None = None
        self.last_frame = 0.0
        self.frames = 0
        self.focus: str | None = None  # mechanic the user chose to practise
        self.log = log_dir / "live_attempts.jsonl"
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("127.0.0.1", FRAME_PORT))
            self.ok = True
        except OSError:
            self.ok = False  # another copy of the app already listens
        if self.ok:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                raw, _ = self.sock.recvfrom(65536)
                msg = json.loads(raw.decode("utf-8", errors="ignore"))
            except (OSError, ValueError):
                continue
            kind = msg.get("type")
            if kind == "hello":
                self.mode, self.player = msg.get("mode"), msg.get("player")
                self.tip(f"RL Coach connected. Practising: {self.focus or 'everything'}.")
                continue
            if kind == "mode":
                self.mode = msg.get("mode")
                continue
            if kind != "frame":
                continue
            self.last_frame = time.time()
            self.frames += 1
            self.mode = msg.get("mode", self.mode)
            for att in self.tracker.feed(msg):
                if self.focus and att.kind != self.focus and att.kind != "landing":
                    continue
                rec = {**asdict(att), "label": att.label, "wall_time": time.time()}
                self.attempts.append(rec)
                try:
                    with self.log.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec) + "\n")
                except OSError:
                    pass
                if att.kind != "landing" or att.score < 50:
                    self.tip(f"{att.label}: {att.grade} ({att.score}). {att.tip}")

    def tip(self, text: str, seconds: float = 6.0):
        try:
            self.out.sendto(json.dumps({"type": "tip", "text": text, "seconds": seconds}).encode(), ("127.0.0.1", TIP_PORT))
        except OSError:
            pass

    def snapshot(self) -> dict:
        hist = list(self.attempts)
        return {
            "listening": self.ok,
            "connected": time.time() - self.last_frame < 3,
            "mode": self.mode,
            "player": self.player,
            "focus": self.focus,
            "attempts": hist[-40:][::-1],
            "summary": summary(self.tracker.history),
        }

    def history(self, limit: int = 2000) -> list[dict]:
        """Attempts from earlier sessions too, for the progress chart."""
        if not self.log.exists():
            return []
        lines = self.log.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out
