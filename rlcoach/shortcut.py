"""A desktop shortcut for RL Coach (Windows).

The first time the app opens it offers to put "RL Coach" on your desktop, and
Settings has a switch for it. The shortcut points at whatever started the app:
RLCoach.exe, or pythonw -m rlcoach.app for copies run from source. Updates
replace RLCoach.exe in place, so the shortcut keeps working; if you move the
exe, the shortcut follows it the next time you open the app from its new home.

Shortcuts are written with PowerShell's WScript.Shell, which every Windows has,
so this needs no extra packages. Paths go in through environment variables so
nothing needs quoting.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "RL Coach.lnk"
SUPPORTED = sys.platform == "win32"


def _powershell(script: str, env: dict[str, str] | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=20, env={**os.environ, **(env or {})},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def desktop_dir() -> Path | None:
    """The real desktop folder, which OneDrive often moves away from ~/Desktop."""
    if not SUPPORTED:
        return None
    d = _powershell("[Environment]::GetFolderPath('Desktop')")
    return Path(d) if d else None


def path() -> Path | None:
    d = desktop_dir()
    return d / NAME if d else None


def launch_target() -> tuple[str, str, str]:
    """(program, arguments, start-in folder) that open this copy of RL Coach."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return str(exe), "", str(exe.parent)
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else py), "-m rlcoach.app", str(Path.cwd())


def exists() -> bool:
    p = path()
    return bool(p and p.is_file())


def create() -> dict:
    if not SUPPORTED:
        return {"ok": False, "error": "Desktop shortcuts are only made on Windows."}
    p = path()
    if p is None:
        return {"ok": False, "error": "Could not find your desktop folder."}
    target, args, workdir = launch_target()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RLC_LNK);"
        "$s.TargetPath = $env:RLC_TARGET; $s.Arguments = $env:RLC_ARGS; $s.WorkingDirectory = $env:RLC_DIR;"
        "$s.IconLocation = $env:RLC_TARGET + ',0'; $s.Description = 'Rocket League replay coach'; $s.Save()"
    )
    env = {"RLC_LNK": str(p), "RLC_TARGET": target, "RLC_ARGS": args, "RLC_DIR": workdir}
    if _powershell(script, env) is None or not p.is_file():
        return {"ok": False, "error": "Windows would not let RL Coach write to your desktop."}
    return {"ok": True, "path": str(p)}


def remove() -> dict:
    p = path()
    if p and p.is_file():
        try:
            p.unlink()
        except OSError as e:
            return {"ok": False, "error": f"Could not remove the shortcut: {e}"}
    return {"ok": True}


def current_target() -> str | None:
    p = path()
    if not (p and p.is_file()):
        return None
    return _powershell("(New-Object -ComObject WScript.Shell).CreateShortcut($env:RLC_LNK).TargetPath", {"RLC_LNK": str(p)})


def repair() -> None:
    """If the exe was moved since the shortcut was made, point the shortcut at where it is now."""
    if not (SUPPORTED and getattr(sys, "frozen", False)):
        return
    t = current_target()
    if t and Path(t) != Path(sys.executable) and not Path(t).is_file():
        create()
