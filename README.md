# rlcoach: Rocket League replay coach

Reads your saved `.replay` files and tells you the three things to work on next, what you did well,
and the exact in-game clock times to rewatch. It only reads replay files from disk. Nothing is
injected into or attached to the game, so it is safe with Easy Anti-Cheat.

Works with any match you can save a replay from: ranked, casual, private matches, and offline
exhibition games against bots.

## The app (recommended)

A desktop window with your replay list on the left (names, mode, map, score, win/loss) and, for each
replay:

- **Coach**: the 3 things to work on, what went well, and clock times to rewatch
- **Heat maps**: where you, your team, the opponents or the ball spent the game, with time per third and half
- **Shots**: a shot map (goal / saved / missed), shooting % for every player and team, and a shot list
- **Stats**: every stat for every player side by side, best in lobby highlighted
- **Boost**: your boost over the whole match with goals marked

Plus **My trends** across all your games (record, what keeps coming up, any stat over time) and
**Rename** to give replays your own names. New replays show up automatically after each match.

### Start it

- **Easiest (Windows):** double-click **`RL Coach.bat`**. The first run installs what it needs
  (needs Python from python.org with "Add python.exe to PATH" ticked); after that it opens straight away.
- **Single .exe:** run `build_windows.bat` once to make `dist\RLCoach.exe`, then double-click that or
  pin it to your taskbar. Once the code is on GitHub, the included GitHub Action builds the .exe for you.
- **Any OS:** `pip install ".[app]"` then run `rlcoach-app`.

The first time you open a replay it takes 5–20 seconds to analyse; after that it is cached and instant.
**Analyse all** does your whole folder in the background. Your settings and cache live in
`%APPDATA%\rlcoach` (Windows) or `~/.rlcoach`.

## Live coaching

Open **🎮 Live** in the app. There are two parts, and neither touches online matches.

### Watch replays together (works with Easy Anti-Cheat on)

Uses Rocket League's official [Stats API](https://www.rocketleague.com/en/developer/stats-api).
Press **Turn on** once (it sets `PacketSendRate` in `TAGame\Config\TAStatsAPI.ini` and keeps a
backup), then restart the game. After that:

- **▶ Watch in game** on any replay opens it in Rocket League's replay viewer.
- While it plays, RL Coach shows what is coming up in the next few seconds (goals against, whiffs,
  lost 50/50s, shots) and has ¼×, ½×, 1× and pause buttons.
- Clicking any moment or shot jumps the in-game replay to a few seconds before it.

### Mechanics trainer (Rocket League started without Easy Anti-Cheat)

Rocket League lets you start the game without Easy Anti-Cheat for training, free play, offline/LAN
matches and replays (Steam asks at launch; on Epic use Library → ⋯ → **Launch without EAC**). In
that mode BakkesMod plugins can run, so RL Coach ships one:

1. Install [BakkesMod](https://bakkesmod.com) and run it once.
2. In RL Coach → Live, press **Install plugin**.
3. Start Rocket League without EAC. The plugin opens RL Coach by itself.
4. Go to Free Play. Every half flip, speed flip, wavedash, fast aerial and long landing gets a score
   out of 100 and one tip, shown on screen in-game and in the Live page (pick one mechanic to focus on).

The plugin (`plugin/RLCoachLive.cpp`) only sends data to `127.0.0.1`, and sends nothing in online
games. Console settings: `rlcoach_enabled`, `rlcoach_overlay`, `rlcoach_autolaunch`, `rlcoach_rate`,
`rlcoach_app_path`.

## Command line

### Install (Windows, macOS, Linux)

Needs Python 3.10 or newer (python.org, tick "Add to PATH" on Windows).

```
cd rl-replay-coach
pip install .
```

### Use

1. After a match, press **Save Replay** on the scoreboard. Replays go to
   `Documents\My Games\Rocket League\TAGame\Demos`.
2. Run:

```
rlcoach                      # analyse your newest replay and open the report
rlcoach -n 10                # your last 10 games, with recurring problems and a game-by-game table
rlcoach --watch              # leave running; every replay you save gets analysed automatically
rlcoach path\to\game.replay  # a specific file
rlcoach -p "YourName"        # pick a player (default: whoever saved the replay)
```

A summary prints in the terminal and a full report opens in your browser (`rlcoach-report.html`).

## What it looks at

| Area | Examples |
| --- | --- |
| Boost | average boost, time at 0 / 100, small vs big pads, boost wasted on full pads or while supersonic, pads stolen |
| Speed | average speed, time supersonic / slow, ground vs air time, powerslides |
| Positioning & rotation | time goal-side of the ball, field thirds, first / last man share |
| Ball play | touches, hard hits, whiffs, beaten to the ball, 50/50s, kickoffs |
| Defence | where you were and how much boost you had on every goal against |
| Mechanics | wavedashes and half flips detected |

Each rule compares you to the other players in the same match (the fairest yardstick, since
matchmaking puts you with similar-skill players) plus a few absolute guardrails. Thresholds live in
`THRESHOLDS` in `rlcoach/coach.py` and are coaching heuristics, not rank data. Against bots, the
lobby comparison is against the bot.

Replay parsing is done by [subtr-actor](https://github.com/rlrml/subtr-actor). A few very old
replays (pre-2017) cannot be parsed.

## Tests

```
pip install .[test]
RLCOACH_TEST_REPLAY=path/to/any.replay pytest
```
