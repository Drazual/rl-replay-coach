"""Friendly names for map codes and playlists stored in replay headers."""

from __future__ import annotations

MAPS = {
    "stadium_p": "DFH Stadium", "stadium_day_p": "DFH Stadium (Day)", "stadium_foggy_p": "DFH Stadium (Stormy)",
    "stadium_winter_p": "DFH Stadium (Snowy)", "stadium_10a_p": "DFH Stadium (Circuit)",
    "eurostadium_p": "Mannfield", "eurostadium_night_p": "Mannfield (Night)", "eurostadium_rainy_p": "Mannfield (Stormy)",
    "eurostadium_snownight_p": "Mannfield (Snowy)", "eurostadium_dusk_p": "Mannfield (Dusk)",
    "park_p": "Beckwith Park", "park_night_p": "Beckwith Park (Midnight)", "park_rainy_p": "Beckwith Park (Stormy)",
    "park_snowy_p": "Beckwith Park (Snowy)",
    "trainstation_p": "Urban Central", "trainstation_night_p": "Urban Central (Night)", "trainstation_dawn_p": "Urban Central (Dawn)",
    "utopiastadium_p": "Utopia Coliseum", "utopiastadium_dusk_p": "Utopia Coliseum (Dusk)",
    "utopiastadium_snow_p": "Utopia Coliseum (Snowy)",
    "cs_p": "Champions Field", "cs_day_p": "Champions Field (Day)", "cs_hw_p": "Rivals Arena",
    "wasteland_p": "Wasteland", "wasteland_night_p": "Wasteland (Night)", "wasteland_s_p": "Wasteland",
    "neotokyo_p": "Neo Tokyo", "neotokyo_standard_p": "Neo Tokyo", "neotokyo_toon_p": "Neo Tokyo (Comic)",
    "arc_p": "Starbase ARC", "arc_standard_p": "Starbase ARC", "arc_darc_p": "Starbase ARC (Aftermath)",
    "farm_p": "Farmstead", "farm_night_p": "Farmstead (Night)", "farm_upsidedown_p": "Farmstead (The Upside Down)",
    "underwater_p": "AquaDome", "underwater_grs_p": "AquaDome (Salty Shallows)",
    "chn_stadium_p": "Forbidden Temple", "chn_stadium_day_p": "Forbidden Temple (Day)",
    "music_p": "Neon Fields", "beach_p": "Salty Shores", "beach_night_p": "Salty Shores (Night)",
    "street_p": "Sovereign Heights", "ff_dusk_p": "Estadio Vida (Dusk)", "outlaw_p": "Deadeye Canyon",
    "outlaw_oasis_p": "Deadeye Canyon (Oasis)", "hoopsstadium_p": "Dunk House", "shattershot_p": "Core 707",
    "labs_basin_p": "Basin", "labs_corridor_p": "Corridor", "labs_utopia_p": "Utopia Retro",
    "throwbackstadium_p": "Throwback Stadium", "ko_calavera_p": "Calavera", "ko_carbon_p": "Carbon",
    "ko_quadron_p": "Quadron", "woods_p": "Drift Woods", "woods_night_p": "Drift Woods (Night)",
    "bb_p": "Champions Field (NFL)", "swoosh_p": "Champions Field (Nike FC)", "fni_stadium_p": "Futura Garden",
}

PLAYLISTS = {
    1: "Duel", 2: "Doubles", 3: "Standard", 4: "Chaos", 6: "Private Match", 7: "Season", 8: "Offline",
    10: "Ranked Duel", 11: "Ranked Doubles", 13: "Ranked Standard", 15: "Snow Day", 16: "Rocket Labs",
    17: "Hoops", 18: "Rumble", 19: "Workshop", 22: "Tournament", 23: "Dropshot", 24: "LAN", 27: "Ranked Hoops",
    28: "Ranked Rumble", 29: "Ranked Dropshot", 30: "Ranked Snow Day", 34: "Tournament", 41: "Heatseeker",
}


def map_name(code: str) -> str:
    c = (code or "").lower()
    return MAPS.get(c) or (code or "Unknown map").replace("_P", "").replace("_p", "").replace("_", " ").title()


def playlist_name(replay_or_meta) -> str:
    gt = getattr(replay_or_meta, "game_type", None)
    if gt is None and isinstance(replay_or_meta, dict):
        gt = replay_or_meta.get("game_type")
    gt = gt or {}
    pid = gt.get("playlist_id")
    if pid in PLAYLISTS:
        return PLAYLISTS[pid]
    return str(gt.get("header_match_type") or gt.get("game_type") or "Match")
