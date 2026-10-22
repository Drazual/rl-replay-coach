"""Check GitHub Releases for a newer version of the app.

Only reads public release info; nothing is downloaded or installed without
the user clicking the link.
"""

from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

REPO = "Drazual/rl-replay-coach"  # change this if the repository lives somewhere else


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def check() -> dict:
    out = {"current": __version__, "available": False}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "rlcoach"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            rel = json.load(r)
    except Exception:
        return out  # offline, no releases yet, or private repo: just stay quiet
    latest = rel.get("tag_name", "")
    exe = next((a["browser_download_url"] for a in rel.get("assets", []) if a.get("name", "").endswith(".exe")), None)
    out.update(latest=latest, url=rel.get("html_url"), exe=exe, notes=(rel.get("body") or "")[:600])
    out["available"] = bool(latest) and _parse(latest) > _parse(__version__)
    return out
