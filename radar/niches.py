"""Niche map: which niche each kept job belongs to.

The taxonomy is a plain JSON list in RADAR_HOME/taxonomy.json. It starts from
a seed of ~40 niches; the Mapper bot grows it every week by naming clusters
in the unmapped pile. Assignment itself is plain keyword matching, so mapping
5,000 jobs costs nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

SEED = Path(__file__).with_name("taxonomy_seed.json")
ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def taxonomy_path(home: Path) -> Path:
    return home / "taxonomy.json"


def load(home: Path) -> list[dict]:
    p = taxonomy_path(home)
    if not p.exists():
        shutil.copy(SEED, p)
    return json.loads(p.read_text())


def save(home: Path, tax: list[dict]) -> None:
    taxonomy_path(home).write_text(json.dumps(tax, indent=1, ensure_ascii=False) + "\n")


def _rx(kw: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])")


class Matcher:
    def __init__(self, tax: list[dict]):
        self.niches = []
        for n in tax:
            kws = [(_rx(k), 2 + len(k.split())) for k in n.get("keywords", []) if k.strip()]
            skills = {s.lower() for s in n.get("skills", [])}
            self.niches.append((n["id"], kws, skills))

    def score(self, title: str, skills: list[str]) -> tuple[str | None, int]:
        t = title.lower()
        js = {s.lower() for s in skills}
        best, best_score = None, 0
        for nid, kws, nskills in self.niches:
            s = sum(w for rx, w in kws if rx.search(t))
            s += len(js & nskills)
            if s > best_score:
                best, best_score = nid, s
        return best, best_score

    def assign(self, title: str, skills: list[str]) -> str | None:
        nid, s = self.score(title, skills)
        # a title keyword (>=3) or two specific skills (2) is enough
        return nid if s >= 2 else None


def apply(con: sqlite3.Connection, tax: list[dict]) -> dict:
    m = Matcher(tax)
    counts: dict[str, int] = {}
    unmapped = 0
    for r in con.execute("SELECT id, title, skills, cut FROM jobs").fetchall():
        nid = None if r["cut"] else m.assign(r["title"], json.loads(r["skills"]))
        con.execute("UPDATE jobs SET niche=? WHERE id=?", (nid, r["id"]))
        if r["cut"]:
            continue
        if nid:
            counts[nid] = counts.get(nid, 0) + 1
        else:
            unmapped += 1
    con.commit()
    return {"mapped": sum(counts.values()), "unmapped": unmapped, "niches": len(counts), "by_niche": counts}


def add(home: Path, nid: str, name: str, keywords: list[str], skills: list[str]) -> dict:
    if not ID_OK.match(nid):
        raise ValueError("niche id: lowercase letters, digits and dashes, 2-41 chars")
    kws = [k.strip() for k in keywords if k.strip()]
    if not kws:
        raise ValueError("give at least one keyword that appears in job titles")
    tax = load(home)
    entry = {"id": nid, "name": name, "keywords": kws, "skills": [s.strip() for s in skills if s.strip()]}
    for i, n in enumerate(tax):
        if n["id"] == nid:
            tax[i] = entry
            break
    else:
        # new niches go first: they are usually more specific than the seed
        tax.insert(0, entry)
    save(home, tax)
    return entry


def unmapped_sample(con: sqlite3.Connection, n: int = 300) -> list[dict]:
    rows = con.execute(
        "SELECT title, skills FROM jobs WHERE cut IS NULL AND niche IS NULL ORDER BY posted_at DESC LIMIT ?", (n,)
    ).fetchall()
    return [{"title": r["title"], "skills": json.loads(r["skills"])} for r in rows]


def names(tax: list[dict]) -> dict[str, str]:
    return {n["id"]: n["name"] for n in tax}
