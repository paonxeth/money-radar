"""The cheap brain: plain rules that cut jobs nobody should chase.

Every cut keeps its reason, so the brief can say exactly why 684 jobs went.
Rules are deliberately conservative: when in doubt, keep the job.
"""

from __future__ import annotations

import re
import sqlite3

SPAM = re.compile(
    r"\b("
    r"unpaid|free trial|test (project|task) only|for free|no budget|"
    r"commission[- ]only|equity[- ]only|profit[- ]shar(e|ing) only|"
    r"(rent|buy|sell)(ing)? (your |an? )?(verified )?accounts?|"
    r"verify (my|an?) account|kyc|fake reviews?|"
    r"deposit|investment opportunity|do not bid|don'?t bid|"
    r"contact me on (telegram|whatsapp)"
    r")\b",
    re.I,
)
NOT_A_GIG = re.compile(
    r"\b(full[- ]time (employee|position|job|role)|permanent (position|role)|"
    r"on[- ]?site|in[- ]person|relocat(e|ion)|agency only|team of \d+)\b",
    re.I,
)
LOCAL_CATEGORIES = {"Local Jobs & Services"}


def value_usd(job: dict | sqlite3.Row, cfg: dict) -> float | None:
    """What one job is worth in USD: mid budget, or mid rate x assumed hours."""
    lo, hi = job["budget_min"], job["budget_max"]
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    mid = (lo + hi) / 2
    if job["type"] == "hourly":
        return mid * cfg.get("hourly_hours", 10)
    return mid


def reason(job: dict | sqlite3.Row, cfg: dict) -> str | None:
    """None if the job stays, otherwise a short reason."""
    title = job["title"] or ""
    if (job["language"] or "en") not in cfg.get("languages", ["en"]):
        return "language"
    if job["local"] or job["category"] in LOCAL_CATEGORIES:
        return "local"
    if SPAM.search(title):
        return "spam"
    if NOT_A_GIG.search(title):
        return "not a gig"
    lo, hi = job["budget_min"], job["budget_max"]
    if not hi and not lo:
        return "no budget"
    if job["type"] == "hourly":
        rate = ((lo or hi) + (hi or lo)) / 2
        if rate < cfg.get("min_hourly_usd", 8):
            return "low rate"
    else:
        if ((lo or hi) + (hi or lo)) / 2 < cfg.get("min_budget_usd", 30):
            return "low budget"
    if (lo or 0) > cfg.get("max_budget_usd", 20000):
        return "team-size"
    return None


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def apply(con: sqlite3.Connection, cfg: dict) -> dict:
    """(Re)classify every job. Duplicate = the same client posting the same title again."""
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    rows = con.execute("SELECT * FROM jobs ORDER BY posted_at, id").fetchall()
    for r in rows:
        why = reason(r, cfg)
        if why is None and r["owner"]:
            key = (r["source"], r["owner"], _norm(r["title"]))
            if key in seen:
                why = "duplicate"
            else:
                seen.add(key)
        con.execute("UPDATE jobs SET cut=? WHERE id=?", (why, r["id"]))
        if why:
            counts[why] = counts.get(why, 0) + 1
    con.commit()
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
