"""Jobs a bot collected with its browser from sites that have no API.

One JSON object per line. Only `title` and `url` are required:

  {"title": "...", "url": "...", "skills": ["Shopify"], "type": "fixed",
   "budget_min": 100, "budget_max": 300, "bids": 7, "posted_at": "2026-10-30"}

Amounts are USD. `bids` means proposals / applicants on the page.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _ts(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def read(path: Path, source: str) -> tuple[list[dict], list[str]]:
    jobs, errors = [], []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {n}: {e}")
            continue
        if not o.get("title") or not o.get("url"):
            errors.append(f"line {n}: title and url are required")
            continue
        native = o.get("id") or hashlib.sha1(o["url"].encode()).hexdigest()[:12]
        bmin, bmax = o.get("budget_min"), o.get("budget_max")
        jobs.append({
            "id": f"{source}:{native}",
            "source": source,
            "title": " ".join(str(o["title"]).split()),
            "skills": list(o.get("skills") or []),
            "category": o.get("category"),
            "type": o.get("type") or "fixed",
            "budget_min": float(bmin) if bmin is not None else None,
            "budget_max": float(bmax if bmax is not None else bmin) if (bmax is not None or bmin is not None) else None,
            "bids": int(o.get("bids") or 0),
            "bid_avg": None,
            "url": o["url"],
            "language": o.get("language") or "en",
            "local": bool(o.get("local")),
            "posted_at": _ts(o.get("posted_at")) or int(time.time()),
        })
    return jobs, errors
