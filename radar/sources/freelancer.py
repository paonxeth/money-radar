"""Freelancer.com public project search. No account, no token.

The active-project endpoint answers without OAuth. Real page size is 100
(often 97-99 come back), and paging stops working around offset ~4,990:
past that every page repeats the last one. We detect that wall and stop.
Extra keyword searches in config["crawl_queries"] go past it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Callable

API = "https://www.freelancer.com/api/projects/0.1/projects/active/"
PAGE = 100
UA = "money-radar/0.1 (+https://github.com/; niche research, read-only)"


def page_url(offset: int, query: str | None = None, limit: int = PAGE) -> str:
    params = [("limit", limit), ("offset", offset), ("job_details", "true"), ("compact", "true")]
    if query:
        params.append(("query", query))
    return API + "?" + urllib.parse.urlencode(params)


def fetch_json(url: str, retries: int = 4, timeout: int = 30) -> dict:
    """GET with backoff on 429 / 5xx. Raises on anything else."""
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def _usd(x, rate: float):
    if x is None:
        return None
    try:
        return round(float(x) * rate, 2)
    except (TypeError, ValueError):
        return None


def normalize(p: dict) -> dict | None:
    """One Freelancer project -> one radar job (amounts in USD)."""
    if not p.get("id") or not p.get("title"):
        return None
    cur = p.get("currency") or {}
    rate = float(cur.get("exchange_rate") or 1.0)
    if rate > 10:  # some payloads give units per USD instead of USD per unit
        rate = 1.0 / rate
    budget = p.get("budget") or {}
    bmin = _usd(budget.get("minimum"), rate)
    bmax = _usd(budget.get("maximum"), rate)
    if bmax is None or bmax == 0:
        bmax = bmin
    stats = p.get("bid_stats") or {}
    jobs = p.get("jobs") or []
    skills = [j["name"] for j in jobs if j.get("name")]
    cats = Counter((j.get("category") or {}).get("name") for j in jobs)
    cats.pop(None, None)
    seo = p.get("seo_url")
    return {
        "id": f"freelancer:{p['id']}",
        "source": "freelancer",
        "owner": str(p["owner_id"]) if p.get("owner_id") else None,
        "title": " ".join(str(p["title"]).split()),
        "skills": skills,
        "category": cats.most_common(1)[0][0] if cats else None,
        "type": p.get("type") or "fixed",
        "budget_min": bmin,
        "budget_max": bmax,
        "bids": int(stats.get("bid_count") or 0),
        "bid_avg": _usd(stats.get("bid_avg"), rate),
        "url": f"https://www.freelancer.com/projects/{seo}" if seo else f"https://www.freelancer.com/projects/{p['id']}",
        "language": p.get("language") or "en",
        "local": bool(p.get("local")),
        "posted_at": int(p.get("time_submitted") or 0) or None,
    }


def _search(query, cap, fetch, delay, on_page, seen_global):
    out, seen, offset = [], set(), 0
    while len(out) < cap:
        data = fetch(page_url(offset, query))
        if data.get("status") not in (None, "success"):
            raise RuntimeError(f"freelancer api: {data.get('message') or data.get('error_code')}")
        projects = (data.get("result") or {}).get("projects") or []
        if not projects:
            break
        ids = {p.get("id") for p in projects}
        if ids <= seen:  # the paging wall: same page again
            break
        seen |= ids
        for p in projects:
            j = normalize(p)
            if j and j["id"] not in seen_global:
                seen_global.add(j["id"])
                out.append(j)
        if on_page:
            on_page(query, offset, len(out))
        offset += PAGE
        if delay:
            time.sleep(delay)
    return out[:cap]


def crawl(cfg: dict, fetch: Callable[[str], dict] | None = None, on_page=None) -> list[dict]:
    """Newest active projects across all categories, then the extra searches."""
    fetch = fetch or fetch_json
    seen: set[str] = set()
    delay = cfg.get("crawl_delay_s", 0.6)
    jobs = _search(None, cfg.get("crawl_max_projects", 5000), fetch, delay, on_page, seen)
    for q in cfg.get("crawl_queries") or []:
        jobs += _search(q, 1000, fetch, delay, on_page, seen)
    return jobs
