"""Keep RL Coach up to date by itself.

On start (and every few hours while open) the app asks GitHub for the latest
release. If it is newer, the new RLCoach.exe is downloaded in the background
and checked against the SHA-256 digest GitHub publishes for it. The swap
happens when you press "Restart to update", or automatically the next time
you open the app. Installs that run from source (RL Coach.bat / pip) update
with pip from the release's source archive instead.

Nothing runs that did not come from this repository's GitHub Releases.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__

REPO = "Drazual/rl-replay-coach"  # change this if the repository lives somewhere else
CHECK_EVERY = 6 * 3600
FROZEN = bool(getattr(sys, "frozen", False))


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "rlcoach"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def latest_release() -> dict | None:
    try:
        rel = _get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    except Exception:
        return None  # offline or no releases yet
    exe = next((a for a in rel.get("assets", []) if a.get("name", "").lower() == "rlcoach.exe"), None)
    return {
        "latest": rel.get("tag_name", ""),
        "url": rel.get("html_url"),
        "notes": (rel.get("body") or "")[:600],
        "exe_url": exe and exe.get("browser_download_url"),
        "exe_sha256": exe and (exe.get("digest") or "").removeprefix("sha256:") or None,
        "source_url": f"https://github.com/{REPO}/archive/refs/tags/{rel.get('tag_name', '')}.zip",
    }


class Updater:
    """Background checker/downloader. The app reads .status() for the banner."""

    def __init__(self, data_dir: Path, enabled: bool = True):
        self.dir = data_dir / "updates"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self.info: dict = {}
        self.state = "idle"  # idle | checking | up_to_date | downloading | ready | error
        self.error = ""
        self.ready_path: Path | None = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ checking
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            if self.enabled:
                self.check_and_download()
            time.sleep(CHECK_EVERY)

    def check_and_download(self) -> None:
        with self.lock:
            if self.state in ("checking", "downloading"):
                return
            self.state = "checking"
        rel = latest_release()
        if rel is None:
            self.state = "idle"
            return
        self.info = rel
        if not rel["latest"] or _parse(rel["latest"]) <= _parse(__version__):
            self.state = "up_to_date"
            return
        if FROZEN:
            self._download_exe(rel)
        else:
            self.state = "ready"  # source installs update with pip at restart time

    def _download_exe(self, rel: dict) -> None:
        target = self.dir / f"RLCoach-{rel['latest']}.exe"
        if target.exists() and _sha256(target) == rel.get("exe_sha256"):
            self.ready_path, self.state = target, "ready"
            return
        if not rel.get("exe_url") or not rel.get("exe_sha256"):
            self.state, self.error = "error", "The release has no verified RLCoach.exe."
            return
        self.state = "downloading"
        part = target.with_suffix(".part")
        try:
            req = urllib.request.Request(rel["exe_url"], headers={"User-Agent": "rlcoach"})
            with urllib.request.urlopen(req, timeout=60) as r, part.open("wb") as fh:
                while chunk := r.read(1 << 16):
                    fh.write(chunk)
            if _sha256(part) != rel["exe_sha256"]:
                part.unlink(missing_ok=True)
                self.state, self.error = "error", "The download did not match GitHub's checksum, so it was thrown away."
                return
            part.replace(target)
            self.ready_path, self.state = target, "ready"
        except Exception as e:
            part.unlink(missing_ok=True)
            self.state, self.error = "error", f"Download failed: {e}"

    def status(self) -> dict:
        return {
            "current": __version__,
            "latest": self.info.get("latest"),
            "available": self.state in ("downloading", "ready"),
            "state": self.state,
            "error": self.error,
            "url": self.info.get("url"),
            "auto": self.enabled,
            "frozen": FROZEN,
        }

    # ------------------------------------------------------------------ applying
    def apply_and_restart(self, extra_args: list[str] | None = None) -> bool:
        """Swap in the new version and start it. The caller should exit right after."""
        args = extra_args or []
        if FROZEN:
            if self.state != "ready" or not self.ready_path:
                return False
            _swap_exe_after_exit(self.ready_path, Path(sys.executable), args)
            return True
        src = self.info.get("source_url")
        if not src:
            return False
        _pip_update_after_exit(src, args)
        return True

    def pending_on_start(self) -> Path | None:
        """A verified download from an earlier run that can be applied before the app opens."""
        if not FROZEN or not self.enabled:
            return None
        best = None
        for f in self.dir.glob("RLCoach-v*.exe"):
            ver = f.stem.removeprefix("RLCoach-")
            if _parse(ver) > _parse(__version__) and (best is None or _parse(ver) > _parse(best[0])):
                best = (ver, f)
        if best is None:
            return None
        # If we already tried to apply this version and are still running the old one, the swap
        # failed (for example the folder is read-only). Don't loop: drop it and let the banner offer it.
        marker = self.dir / f"attempted-{best[0]}"
        if marker.exists():
            marker.unlink(missing_ok=True)
            best[1].unlink(missing_ok=True)
            return None
        marker.write_text(str(time.time()))
        self.ready_path, self.state, self.info = best[1], "ready", {"latest": best[0]}
        return best[1]

    def cleanup(self) -> None:
        """Remove downloads for versions we already run."""
        for f in self.dir.glob("RLCoach-v*.exe"):
            if _parse(f.stem.removeprefix("RLCoach-")) <= _parse(__version__):
                f.unlink(missing_ok=True)
        for f in self.dir.glob("attempted-v*"):
            if _parse(f.name.removeprefix("attempted-")) <= _parse(__version__):
                f.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _detached(cmd: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)


def _swap_exe_after_exit(new: Path, current: Path, args: list[str]) -> None:
    """A tiny script waits for this process to exit, replaces the exe, and starts it again."""
    pid = os.getpid()
    script = Path(tempfile.gettempdir()) / f"rlcoach-update-{pid}.cmd"
    quoted = " ".join(f'"{a}"' for a in args)
    script.write_text("\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        "set tries=0",
        ":move",
        f'move /Y "{new}" "{current}" >nul 2>nul && goto run',
        "set /a tries+=1",
        "if %tries% GEQ 20 goto run",
        "timeout /t 1 /nobreak >nul",
        "goto move",
        ":run",
        f'start "" "{current}" {quoted}',
        'del "%~f0"',
    ]) + "\r\n", encoding="utf-8")
    _detached(["cmd.exe", "/c", str(script)])


def _pip_update_after_exit(source_url: str, args: list[str]) -> None:
    """Source installs: pip-install the release's source archive, then start the app again."""
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    code = (
        "import os,subprocess,sys,time\n"
        f"pid={os.getpid()}\n"
        "def alive(p):\n"
        "    try:\n"
        "        os.kill(p, 0)\n"
        "        return sys.platform != 'win32' or subprocess.run(['tasklist','/FI',f'PID eq {p}'],capture_output=True,text=True).stdout.count(str(p))>0\n"
        "    except OSError:\n"
        "        return False\n"
        "for _ in range(60):\n"
        "    if not alive(pid): break\n"
        "    time.sleep(1)\n"
        f"subprocess.run([sys.executable,'-m','pip','install','--upgrade',{'rlcoach[app] @ ' + source_url!r}])\n"
        f"subprocess.Popen([{str(pyw if pyw.exists() else py)!r},'-m','rlcoach.app',*{args!r}])\n"
    )
    _detached([str(py), "-c", code])
