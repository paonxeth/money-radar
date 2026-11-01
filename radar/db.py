"""SQLite store. One file, no server, survives between routine runs."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,          -- "<source>:<native id>"
  source TEXT NOT NULL,
  owner TEXT,                   -- client id when the source has one (for duplicate detection)
  title TEXT NOT NULL,
  skills TEXT NOT NULL DEFAULT '[]',
  category TEXT,
  type TEXT,                    -- fixed | hourly
  budget_min REAL,              -- USD
  budget_max REAL,              -- USD (per hour for hourly)
  bids_first INTEGER,           -- bid count when first seen
  bids INTEGER,                 -- latest bid count
  bid_avg REAL,                 -- USD
  url TEXT,
  language TEXT,
  local INTEGER DEFAULT 0,
  posted_at INTEGER,            -- unix seconds
  first_seen INTEGER,
  last_seen INTEGER,
  cut TEXT,                     -- NULL = kept, else the reason it was cut
  niche TEXT                    -- niche id, NULL = unmapped
);
CREATE INDEX IF NOT EXISTS jobs_posted ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS jobs_niche ON jobs(niche);

CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started INTEGER, finished INTEGER, stats TEXT
);

CREATE TABLE IF NOT EXISTS verdicts(
  niche TEXT NOT NULL,
  ts INTEGER NOT NULL,
  verdict TEXT NOT NULL,        -- real | crowded | unclear
  note TEXT,
  market TEXT,                  -- what the same work sells for elsewhere
  evidence TEXT,                -- JSON list of URLs
  potential REAL,               -- potential at the time of the check
  PRIMARY KEY(niche, ts)
);

CREATE TABLE IF NOT EXISTS events(
  ts INTEGER NOT NULL,
  agent TEXT NOT NULL,
  msg TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert_jobs(con: sqlite3.Connection, jobs: list[dict], now: int | None = None) -> tuple[int, int]:
    """Insert new jobs, refresh bids/last_seen on known ones. Returns (new, updated)."""
    now = now or int(time.time())
    new = upd = 0
    for j in jobs:
        row = con.execute("SELECT id FROM jobs WHERE id=?", (j["id"],)).fetchone()
        if row:
            con.execute(
                "UPDATE jobs SET bids=?, bid_avg=?, last_seen=?, budget_min=?, budget_max=? WHERE id=?",
                (j.get("bids"), j.get("bid_avg"), now, j.get("budget_min"), j.get("budget_max"), j["id"]),
            )
            upd += 1
        else:
            con.execute(
                """INSERT INTO jobs(id, source, owner, title, skills, category, type, budget_min, budget_max,
                   bids_first, bids, bid_avg, url, language, local, posted_at, first_seen, last_seen)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    j["id"], j["source"], j.get("owner"), j["title"], json.dumps(j.get("skills") or []),
                    j.get("category"), j.get("type"), j.get("budget_min"), j.get("budget_max"),
                    j.get("bids"), j.get("bids"), j.get("bid_avg"), j.get("url"),
                    j.get("language"), 1 if j.get("local") else 0,
                    j.get("posted_at") or now, now, now,
                ),
            )
            new += 1
    con.commit()
    return new, upd


def log(con: sqlite3.Connection, agent: str, msg: str, ts: int | None = None) -> None:
    con.execute("INSERT INTO events(ts, agent, msg) VALUES(?,?,?)", (ts or int(time.time()), agent, msg))
    con.commit()


def get_meta(con: sqlite3.Connection, k: str, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def set_meta(con: sqlite3.Connection, k: str, v) -> None:
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (k, json.dumps(v)))
    con.commit()
