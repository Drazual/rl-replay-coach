"""Command line entry point: `rlcoach [replays or folders] [--player NAME]`."""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

from . import parse
from .coach import coach
from .report import to_html, to_text


def default_replay_dir() -> Path | None:
    """Where Rocket League saves replays on this machine, if we can find it."""
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", home)) / "Documents/My Games/Rocket League/TAGame/Demos",
        home / "OneDrive/Documents/My Games/Rocket League/TAGame/Demos",
        home / "Documents/My Games/Rocket League/TAGame/Demos",
        # Steam Proton on Linux
        home / ".steam/steam/steamapps/compatdata/252950/pfx/drive_c/users/steamuser/Documents/My Games/Rocket League/TAGame/Demos",
    ]
    return next((c for c in candidates if c.is_dir()), None)


def collect(paths: list[str], last: int) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(p.glob("*.replay"), key=lambda f: f.stat().st_mtime)[-last:]
        elif p.suffix == ".replay":
            files.append(p)
    return files


def analyse(files: list[Path], player: str | None, quiet: bool = False) -> list:
    reports = []
    for f in files:
        if not quiet:
            print(f"Analysing {f.name} ...", file=sys.stderr)
        try:
            reports.append(coach(parse.load(f), player))
        except LookupError as e:
            print(f"  skipped: {e}", file=sys.stderr)
        except Exception as e:  # a single unreadable replay should not stop a batch
            print(f"  skipped {f.name}: could not parse ({e})", file=sys.stderr)
    reports.sort(key=lambda r: r.replay.date)
    return reports


def write_report(reports, out: Path, open_browser: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_html(reports), encoding="utf-8")
    print(f"Report written to {out}", file=sys.stderr)
    if open_browser:
        webbrowser.open(out.resolve().as_uri())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rlcoach", description="Coaching feedback from Rocket League replay files.")
    ap.add_argument("paths", nargs="*", help=".replay files or folders (default: your Rocket League Demos folder)")
    ap.add_argument("-p", "--player", help="your in-game name (default: whoever saved the replay)")
    ap.add_argument("-n", "--last", type=int, default=1, help="when given a folder, analyse the newest N replays (default 1)")
    ap.add_argument("-o", "--out", type=Path, default=Path("rlcoach-report.html"), help="where to write the HTML report")
    ap.add_argument("--no-open", action="store_true", help="do not open the report in a browser")
    ap.add_argument("--watch", action="store_true", help="keep running and analyse each new replay as it is saved")
    args = ap.parse_args(argv)

    paths = args.paths
    if not paths:
        d = default_replay_dir()
        if d is None:
            ap.error("could not find your Demos folder; pass a .replay file or folder")
        paths = [str(d)]

    if args.watch:
        return watch([Path(p) for p in paths if Path(p).is_dir()], args)

    files = collect(paths, args.last)
    if not files:
        print("No .replay files found.", file=sys.stderr)
        return 1
    reports = analyse(files, args.player)
    if not reports:
        return 1
    print(to_text(reports))
    write_report(reports, args.out, not args.no_open)
    return 0


def watch(dirs: list[Path], args) -> int:
    if not dirs:
        print("--watch needs a folder.", file=sys.stderr)
        return 1
    seen = {f for d in dirs for f in d.glob("*.replay")}
    print(f"Watching {', '.join(map(str, dirs))} for new replays. Press Ctrl+C to stop.", file=sys.stderr)
    try:
        while True:
            time.sleep(3)
            new = sorted({f for d in dirs for f in d.glob("*.replay")} - seen, key=lambda f: f.stat().st_mtime)
            for f in new:
                time.sleep(2)  # let the game finish writing the file
                seen.add(f)
                reports = analyse([f], args.player)
                if reports:
                    print(to_text(reports))
                    write_report(reports, args.out, not args.no_open)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
