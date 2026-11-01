"""data.json for the dashboard, and a self-contained dashboard.html."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db, score

WEB = Path(__file__).with_name("web")

AGENTS = [
    {"id": "scout", "name": "Scout", "bot": True, "routine": "daily 06:00",
     "role": "reads every open job on the market"},
    {"id": "gate", "name": "Gate", "bot": False, "routine": "inside Scout's run",
     "role": "plain python, cuts junk, costs $0"},
    {"id": "mapper", "name": "Mapper", "bot": True, "routine": "Sun 05:00",
     "role": "names new niches, sorts jobs into them"},
    {"id": "skeptic", "name": "Skeptic", "bot": True, "routine": "daily 06:30",
     "role": "checks the open web, kills crowded niches"},
    {"id": "analyst", "name": "Analyst", "bot": True, "routine": "Mon 09:00",
     "role": "weekly trends, simulated freelancer"},
    {"id": "briefer", "name": "Briefer", "bot": True, "routine": "daily 08:00",
     "role": "writes the 3-niche morning brief"},
]


def build(con, cfg, names: dict, run_stats: dict, sim: dict, now: int | None = None) -> dict:
    now = now or int(time.time())
    b = score.board(con, cfg, now)
    niches = []
    for s in b["niches"][:30]:
        niches.append({k: s[k] for k in (
            "id", "jobs_week", "median_value", "median_bids", "potential_month",
            "trend", "eligible", "verdict", "market", "note")} | {"name": names.get(s["id"], s["id"])})
    days = []
    start = (now // 86400 - 13) * 86400
    for i in range(14):
        d0 = start + i * 86400
        r = con.execute(
            "SELECT COUNT(*) n, SUM(cut IS NULL) k FROM jobs WHERE posted_at >= ? AND posted_at < ?",
            (d0, d0 + 86400),
        ).fetchone()
        days.append({"day": datetime.fromtimestamp(d0, timezone.utc).strftime("%m-%d"),
                     "posted": r["n"] or 0, "kept": r["k"] or 0})
    events = [dict(r) for r in con.execute(
        "SELECT ts, agent, msg FROM events ORDER BY ts DESC, rowid DESC LIMIT 80").fetchall()][::-1]
    bot_runs = {r["agent"]: r["n"] for r in con.execute(
        "SELECT agent, COUNT(*) n FROM events GROUP BY agent").fetchall()}
    samples = {nid: score.samples(con, nid, 4, now, b["window_days"]) for nid in b["top"]}
    return {
        "generated": now,
        "day": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"),
        "demo": bool(db.get_meta(con, "demo", False)),
        "pipeline": run_stats.get("pipeline", {}),
        "typical_bids": b["typical_bids"],
        "window_days": b["window_days"],
        "niches": niches,
        "top": b["top"],
        "crowded": b["crowded"],
        "candidates": b["candidates"],
        "days": days,
        "sim": sim,
        "agents": AGENTS,
        "events": events,
        "event_counts": bot_runs,
        "samples": samples,
        "runs": con.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"],
    }


def write(home: Path, data: dict) -> tuple[Path, Path]:
    out = home / "out"
    out.mkdir(exist_ok=True)
    dj = out / "data.json"
    dj.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    html = (WEB / "dashboard.html").read_text()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = out / "dashboard.html"
    page.write_text(html.replace("/*__RADAR_DATA__*/null", blob))
    return dj, page
