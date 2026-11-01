"""Morning brief and weekly report, as markdown the Briefer bot polishes."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import score


def money(x) -> str:
    return f"${x:,.0f}" if x is not None else "-"


def _trend(t) -> str:
    return "" if t is None else f"{t * 100:+.0f}% vs last week"


def _line(s: dict, names: dict) -> str:
    t = _trend(s["trend"])
    return (
        f"{s['jobs_week']:.0f} jobs/week · median {money(s['median_value'])} · "
        f"{s['median_bids']:.0f} bids each" + (f" · {t}" if t else "")
    )


def build(con, cfg, names: dict, run_stats: dict, sim: dict, now: int | None = None) -> tuple[str, dict]:
    now = now or int(time.time())
    b = score.board(con, cfg, now)
    by_id = {s["id"]: s for s in b["niches"]}
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    p = run_stats.get("pipeline", {})
    md = [f"# Money Radar · {day}", ""]
    md.append(
        f"Read {p.get('read', 0):,} open jobs · cut {p.get('cut', 0):,} · "
        f"mapped {p.get('mapped', 0):,} into {p.get('niches', 0)} niches · "
        f"Skeptic checked {p.get('checked', 0)} · {len(b['top'])} survived"
    )
    md.append(f"Typical job right now: {b['typical_bids']:.0f} bids.")
    if not b["trend_ok"]:
        md.append("Week-over-week trends start after 7 days of daily runs.")
    md.append("")
    top = []
    for i, nid in enumerate(b["top"], 1):
        s = by_id[nid]
        name = names.get(nid, nid)
        md.append(f"## {i}. {name} · ~{money(s['potential_month'])}/mo potential")
        md.append(_line(s, names))
        if s["verdict"]:
            md.append(f"Skeptic: **{s['verdict']}**" + (f" · elsewhere: {s['market']}" if s["market"] else "")
                      + (f" · {s['note']}" if s["note"] else ""))
        else:
            md.append("Skeptic: not checked yet")
        md.append("")
        jobs = score.samples(con, nid, 3, now, b["window_days"])
        for j in jobs:
            rng = money(j["budget_min"]) if j["budget_min"] == j["budget_max"] else f"{money(j['budget_min'])}-{money(j['budget_max'])}"
            unit = "/h" if j["type"] == "hourly" else ""
            md.append(f"- [{j['title']}]({j['url']}) · {rng}{unit} · {j['bids']} bids")
        md.append("")
        md.append("First move: <Briefer: one gig title + one opening line for a proposal>")
        md.append("")
        top.append(dict(s, name=name, samples=jobs))
    if b["crowded"]:
        md.append("## Looked good here, crowded elsewhere")
        for nid in b["crowded"]:
            s = by_id[nid]
            md.append(f"- {names.get(nid, nid)}: {s['note'] or 'crowded on other platforms'}")
        md.append("")
    heating = sorted(
        (s for s in b["niches"] if s["trend"] is not None and s["jobs_week"] >= cfg.get("min_jobs_per_week", 15)),
        key=lambda s: -s["trend"],
    )[:2]
    if heating and heating[0]["trend"] > 0.1:
        md.append("## Heating up")
        for s in heating:
            if s["trend"] > 0.1:
                md.append(f"- {names.get(s['id'], s['id'])}: {_trend(s['trend'])}")
        md.append("")
    if sim.get("days"):
        md.append("## Simulated freelancer (SIMULATED money, not income)")
        md.append(
            f"Since {sim['days'][0]}: radar {money(sim['radar'][-1])} vs random {money(sim['random'][-1])} "
            f"(same rules: {sim['params']['bids_per_day']} bids/day, max {sim['params']['max_active']} jobs at once)."
        )
        md.append("")
    data = {"day": day, "pipeline": p, "typical_bids": b["typical_bids"], "top": top,
            "crowded": b["crowded"], "heating": [s["id"] for s in heating]}
    return "\n".join(md).rstrip() + "\n", data


def weekly(con, cfg, names: dict, sim: dict, now: int | None = None) -> str:
    now = now or int(time.time())
    b = score.board(con, cfg, now)
    md = ["# Money Radar · weekly", ""]
    if not b["trend_ok"]:
        md += ["Trends need 7 days of daily runs first; numbers below are this week only.", ""]
    rising = sorted((s for s in b["niches"] if s["trend"] is not None), key=lambda s: -s["trend"])
    md.append("## Heating up")
    for s in rising[:5]:
        md.append(f"- {names.get(s['id'], s['id'])}: {_line(s, names)}")
    md.append("")
    md.append("## Cooling down")
    for s in rising[::-1][:5]:
        md.append(f"- {names.get(s['id'], s['id'])}: {_line(s, names)}")
    md.append("")
    runs = con.execute("SELECT COUNT(*) n FROM runs WHERE started >= ?", (now - 7 * 86400,)).fetchone()["n"]
    unmapped = con.execute("SELECT COUNT(*) n FROM jobs WHERE cut IS NULL AND niche IS NULL").fetchone()["n"]
    kept = con.execute("SELECT COUNT(*) n FROM jobs WHERE cut IS NULL").fetchone()["n"]
    md.append("## Health")
    md.append(f"- radar runs this week: {runs}")
    md.append(f"- unmapped kept jobs: {unmapped:,} of {kept:,} ({(unmapped / kept * 100) if kept else 0:.0f}%)")
    if sim.get("days"):
        md.append(f"- simulated: radar {money(sim['radar'][-1])} vs random {money(sim['random'][-1])} since {sim['days'][0]}")
    return "\n".join(md) + "\n"
