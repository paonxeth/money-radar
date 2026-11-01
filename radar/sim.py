"""Simulated freelancer. SIMULATED MONEY, not income.

Replays every day the radar has data for, with two paper freelancers who
follow identical rules and differ only in which jobs they bid on:

  radar   bids on the best-value jobs posted that day in the radar's top 3
          niches, where the top 3 is computed only from jobs posted BEFORE
          that day (no look-ahead)
  random  bids on random jobs posted that day

Rules for both: `bids_per_day` bids, a win chance of
win_factor / (max(bids_on_job, min_competitors) + 1), at most `max_active`
jobs at once, paid the job's value `delivery_days` after winning. The radar
freelancer picks the highest-value jobs in its niches without looking at
their bid counts, so the only edge it gets is the choice of niche. Wins are drawn from a hash of
(seed, job id), so a replay always gives the same result.

Bid counts are the latest ones we saw, usually higher than on posting day,
which makes the simulation conservative.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from datetime import datetime, timezone

from . import filters, score

DAY = 86400


def _u(seed, jid: str, salt: str) -> float:
    h = hashlib.sha256(f"{seed}:{jid}:{salt}".encode()).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _day(ts: int) -> int:
    return ts // DAY * DAY


def run(con: sqlite3.Connection, cfg: dict, now: int | None = None) -> dict:
    now = now or int(time.time())
    sc = cfg["sim"]
    rows = con.execute("SELECT * FROM jobs WHERE cut IS NULL AND posted_at IS NOT NULL ORDER BY posted_at").fetchall()
    out = {"days": [], "radar": [], "random": [], "wins": [], "params": dict(sc)}
    if not rows:
        return out
    first = _day(rows[0]["posted_at"])
    last = _day(now)
    w = cfg.get("window_days", 7)
    by_day: dict[int, list] = {}
    for r in rows:
        by_day.setdefault(_day(r["posted_at"]), []).append(r)

    state = {k: {"balance": 0.0, "active": []} for k in ("radar", "random")}
    d = first
    started = False
    seen_before = 0
    while d <= last:
        todays = by_day.get(d, [])
        if not started and seen_before >= sc.get("start_after_jobs", 300):
            started = True
        if started:
            span = max(1, min(w, (d - first) // DAY))
            hist = [r for r in rows if d - span * DAY <= r["posted_at"] < d]
            stats, typical = score.niche_stats(hist, cfg, span)
            eligible = [
                (nid, s) for nid, s in stats.items()
                if s["jobs_week"] >= cfg.get("min_jobs_per_week", 15) and s["median_bids"] < typical
            ]
            eligible.sort(key=lambda x: -x[1]["potential_month"])
            top = {nid for nid, _ in eligible[:3]}

            def p_win(r):
                return sc["win_factor"] / (max(r["bids"] or 0, sc.get("min_competitors", 3)) + 1)

            picks = {
                "radar": sorted(
                    (r for r in todays if r["niche"] in top),
                    key=lambda r: -(filters.value_usd(r, cfg) or 0),
                ),
                "random": sorted(todays, key=lambda r: _u(sc["seed"], r["id"], "order")),
            }
            for k, st in state.items():
                # get paid for finished work
                still = []
                for due, val in st["active"]:
                    if due <= d:
                        st["balance"] += val
                    else:
                        still.append((due, val))
                st["active"] = still
                for r in picks[k][: sc["bids_per_day"]]:
                    if len(st["active"]) >= sc["max_active"]:
                        break
                    if _u(sc["seed"], r["id"], "win") < p_win(r):
                        val = round(filters.value_usd(r, cfg) or 0, 2)
                        st["active"].append((d + sc["delivery_days"] * DAY, val))
                        out["wins"].append({
                            "day": datetime.fromtimestamp(d, timezone.utc).strftime("%Y-%m-%d"),
                            "strategy": k, "title": r["title"], "niche": r["niche"],
                            "value": val, "bids": r["bids"], "url": r["url"],
                        })
            out["days"].append(datetime.fromtimestamp(d, timezone.utc).strftime("%Y-%m-%d"))
            out["radar"].append(round(state["radar"]["balance"], 2))
            out["random"].append(round(state["random"]["balance"], 2))
        seen_before += len(todays)
        d += DAY
    return out
