"""Niche numbers. All arithmetic, no model calls.

For each niche over the last `window_days` (by posting time):
  jobs           how many kept jobs were posted
  median_value   median job value in USD
  median_bids    median number of bids already on those jobs
  potential      what one person could expect to earn here with
                 `bids_per_week` proposals: bid on the jobs with the best
                 value / (bids + 1), win each with chance 1 / (bids + 1)
                 (at least `min_competitors` assumed), never more than
                 `capacity_jobs_per_week` wins. Weekly, x 30/7 for a month.

Crowded niches score low even with lots of volume, because 20 proposals
into jobs with 48 bids each win about 0.4 jobs a week.

A niche is a candidate when it has real volume (>= min_jobs_per_week) and
fewer bids than the typical job across the whole market.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time

from . import filters

DAY = 86400


def _median(xs):
    return float(statistics.median(xs)) if xs else 0.0


def window_rows(con: sqlite3.Connection, start: int, end: int):
    return con.execute(
        "SELECT * FROM jobs WHERE cut IS NULL AND posted_at >= ? AND posted_at < ?", (start, end)
    ).fetchall()


def niche_stats(rows, cfg: dict, window_days: int) -> tuple[dict, float]:
    per: dict[str, dict] = {}
    all_bids = []
    for r in rows:
        all_bids.append(r["bids"] or 0)
        if not r["niche"]:
            continue
        v = filters.value_usd(r, cfg)
        if v is None:
            continue
        d = per.setdefault(r["niche"], {"values": [], "bids": [], "fair": 0.0})
        b = r["bids"] or 0
        d["values"].append(v)
        d["bids"].append(b)
    typical = _median(all_bids)
    out = {}
    scale = 7.0 / window_days
    cap_jobs = cfg.get("capacity_jobs_per_week", 2)
    min_comp = cfg.get("sim", {}).get("min_competitors", 3)
    n_bids = max(1, round(cfg.get("bids_per_week", 20) * window_days / 7))
    for nid, d in per.items():
        mv = _median(d["values"])
        jobs = sorted(((v, 1.0 / (max(b, min_comp) + 1)) for v, b in zip(d["values"], d["bids"])),
                      key=lambda vp: -vp[0] * vp[1])[:n_bids]
        wins = sum(p for _, p in jobs) * scale
        income = sum(v * p for v, p in jobs) * scale
        if wins > cap_jobs:
            income *= cap_jobs / wins
            wins = cap_jobs
        out[nid] = {
            "jobs": len(d["values"]),
            "jobs_week": round(len(d["values"]) * scale, 1),
            "median_value": round(mv, 2),
            "median_bids": round(_median(d["bids"]), 1),
            "wins_week": round(wins, 2),
            "potential_month": round(income * 30 / 7, 2),
        }
    return out, typical


def latest_verdicts(con: sqlite3.Connection) -> dict[str, dict]:
    rows = con.execute(
        "SELECT v.* FROM verdicts v JOIN (SELECT niche, MAX(ts) ts FROM verdicts GROUP BY niche) m "
        "ON v.niche = m.niche AND v.ts = m.ts"
    ).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"] or "[]")
        out[r["niche"]] = d
    return out


def board(con: sqlite3.Connection, cfg: dict, now: int | None = None) -> dict:
    """The full ranked picture the bots and the dashboard read."""
    now = now or int(time.time())
    w = cfg.get("window_days", 7)
    cur, typical = niche_stats(window_rows(con, now - w * DAY, now), cfg, w)
    prev, _ = niche_stats(window_rows(con, now - 2 * w * DAY, now - w * DAY), cfg, w)
    verdicts = latest_verdicts(con)
    # Last week's numbers only mean something if we were crawling last week:
    # jobs that were awarded before our first crawl are invisible to us.
    since = con.execute("SELECT MIN(first_seen) m FROM jobs").fetchone()["m"]
    trend_ok = since is not None and since <= now - w * DAY
    niches = []
    for nid, s in cur.items():
        p = prev.get(nid)
        s = dict(s, id=nid)
        s["trend"] = round((s["jobs"] - p["jobs"]) / p["jobs"], 3) if trend_ok and p and p["jobs"] else None
        s["eligible"] = s["jobs_week"] >= cfg.get("min_jobs_per_week", 15) and s["median_bids"] < typical
        v = verdicts.get(nid)
        s["verdict"] = v["verdict"] if v else None
        s["market"] = v["market"] if v else None
        s["note"] = v["note"] if v else None
        s["verdict_ts"] = v["ts"] if v else None
        s["evidence"] = v["evidence"] if v else []
        niches.append(s)
    niches.sort(key=lambda s: (-s["eligible"], -s["potential_month"]))
    eligible = [s for s in niches if s["eligible"]]
    checked = eligible[: cfg.get("verify_top", 12)]
    top = [s for s in checked if s["verdict"] != "crowded"]
    top.sort(key=lambda s: (s["verdict"] != "real", -s["potential_month"]))
    return {
        "now": now,
        "trend_ok": trend_ok,
        "window_days": w,
        "typical_bids": typical,
        "niches": niches,
        "candidates": [s["id"] for s in checked],
        "top": [s["id"] for s in top[:3]],
        "crowded": [s["id"] for s in checked if s["verdict"] == "crowded"],
    }


def queue(con: sqlite3.Connection, cfg: dict, now: int | None = None) -> list[dict]:
    """Niches the Skeptic should check today, cheapest first: only new or changed ones."""
    now = now or int(time.time())
    b = board(con, cfg, now)
    by_id = {s["id"]: s for s in b["niches"]}
    ttl = cfg.get("verdict_ttl_days", 7) * DAY
    change = cfg.get("recheck_change", 0.25)
    verdicts = latest_verdicts(con)
    todo = []
    for nid in b["candidates"]:
        s = by_id[nid]
        v = verdicts.get(nid)
        if v:
            fresh = now - v["ts"] < ttl
            old_p = v["potential"] or 0
            moved = old_p and abs(s["potential_month"] - old_p) / old_p > change
            if fresh and not moved:
                continue
        todo.append(s)
    return todo[: cfg.get("verify_per_day", 5)]


def samples(con: sqlite3.Connection, nid: str, n: int = 5, now: int | None = None, window_days: int = 7):
    now = now or int(time.time())
    rows = con.execute(
        "SELECT title, url, type, budget_min, budget_max, bids FROM jobs "
        "WHERE niche=? AND cut IS NULL AND posted_at >= ? ORDER BY posted_at DESC LIMIT ?",
        (nid, now - window_days * DAY, n),
    ).fetchall()
    return [dict(r) for r in rows]


def add_verdict(con, cfg, nid: str, verdict: str, note: str, market: str, evidence: list[str], now=None) -> dict:
    if verdict not in ("real", "crowded", "unclear"):
        raise ValueError("verdict must be real, crowded or unclear")
    now = now or int(time.time())
    b = board(con, cfg, now)
    s = next((x for x in b["niches"] if x["id"] == nid), None)
    pot = s["potential_month"] if s else None
    con.execute(
        "INSERT OR REPLACE INTO verdicts(niche, ts, verdict, note, market, evidence, potential) VALUES(?,?,?,?,?,?,?)",
        (nid, now, verdict, note, market, json.dumps(evidence), pot),
    )
    con.commit()
    return {"niche": nid, "verdict": verdict, "potential_month": pot}
