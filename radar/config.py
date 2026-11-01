"""Paths and settings. Everything durable lives under RADAR_HOME.

On a Grok Bot cloud computer the default is /workspace/money-radar/data,
because /workspace survives recovery and packages may not.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    # filter: the cheap brain
    "min_budget_usd": 30,          # fixed-price jobs below this are cut
    "min_hourly_usd": 8,           # hourly jobs below this rate are cut
    "max_budget_usd": 20000,       # above this it's a team project, not one person
    "hourly_hours": 10,            # hours assumed per hourly job when valuing it
    "languages": ["en"],
    # scoring
    "window_days": 7,
    "min_jobs_per_week": 15,       # a niche needs real volume to count
    "capacity_jobs_per_week": 2,   # one person can realistically deliver this many
    "bids_per_week": 20,           # proposals one person can realistically send
    # skeptic
    "verify_top": 12,              # how many niches go to the Skeptic
    "verify_per_day": 5,           # max web checks per day (usage!)
    "verdict_ttl_days": 7,         # re-check a niche after this many days
    "recheck_change": 0.25,        # ...or when its potential moved this much
    # crawl
    "crawl_max_projects": 5000,    # Freelancer stops paging around offset ~4,990
    "crawl_queries": [],           # extra keyword searches to go past the wall
    "crawl_delay_s": 0.6,
    # simulated freelancer
    "sim": {
        "bids_per_day": 4,
        "win_factor": 0.5,         # newcomer penalty on the fair share 1/(bids+1)
        "delivery_days": 3,
        "max_active": 2,
        "min_competitors": 3,      # assume at least this many other bids, even on a fresh job
        "start_after_jobs": 300,   # need this many kept jobs before the first sim day
        "seed": 7,
    },
}


def home() -> Path:
    env = os.environ.get("RADAR_HOME")
    if env:
        p = Path(env)
    elif Path("/workspace").is_dir() and os.access("/workspace", os.W_OK):
        p = Path("/workspace/money-radar/data")
    else:
        p = Path.cwd() / "radar-data"
    p.mkdir(parents=True, exist_ok=True)
    (p / "out").mkdir(exist_ok=True)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path | None = None) -> dict:
    """DEFAULTS overlaid with RADAR_HOME/config.json if it exists."""
    path = path or home() / "config.json"
    if path.exists():
        with open(path) as f:
            return _merge(DEFAULTS, json.load(f))
    return copy.deepcopy(DEFAULTS)
