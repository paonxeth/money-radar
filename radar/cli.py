"""Command line. Bots call this; every bot-facing command can print JSON.

  python3 -m radar doctor              check the box, the folder and the network
  python3 -m radar run                 Scout's routine: crawl -> gate -> map -> score -> sim -> export
  python3 -m radar queue               niches the Skeptic should check today (JSON)
  python3 -m radar verdict ID ...      Skeptic records what it found
  python3 -m radar brief               the morning brief (markdown)
  python3 -m radar unmapped            jobs no niche matched, for the Mapper (JSON)
  python3 -m radar taxonomy add ...    Mapper adds or edits a niche
  python3 -m radar remap               re-run gate + map + export without crawling
  python3 -m radar weekly              Analyst's weekly report
  python3 -m radar board               ranked niches as a table
  python3 -m radar ingest FILE --source NAME   jobs a bot collected with its browser
  python3 -m radar log AGENT MESSAGE   a bot writes one line to the shared log
  python3 -m radar export              out/data.json + out/dashboard.html
  python3 -m radar demo                fill a separate folder with demo data
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__, brief, config, db, export, filters, niches, score, sim
from .sources import freelancer, inbox


def _open(args):
    if getattr(args, "home", None):
        import os
        os.environ["RADAR_HOME"] = args.home
    home = config.home()
    cfg = config.load(home / "config.json")
    con = db.connect(home / "radar.db")
    return home, cfg, con


def _now(con: sqlite3.Connection) -> int:
    """Demo folders are frozen at their own date; real folders use the clock."""
    return db.get_meta(con, "demo_now") or int(time.time())


def _pipeline(con: sqlite3.Connection, b: dict) -> dict:
    last = con.execute("SELECT MAX(last_seen) m FROM jobs").fetchone()["m"]
    if last is None:
        return {"read": 0, "cut": 0, "cut_reasons": {}, "kept": 0, "mapped": 0, "unmapped": 0,
                "niches": 0, "checked": 0, "survived": 0}
    rows = con.execute("SELECT cut, niche FROM jobs WHERE last_seen >= ?", (last - 3600,)).fetchall()
    reasons: dict[str, int] = {}
    kept = mapped = 0
    nset = set()
    for r in rows:
        if r["cut"]:
            reasons[r["cut"]] = reasons.get(r["cut"], 0) + 1
        else:
            kept += 1
            if r["niche"]:
                mapped += 1
                nset.add(r["niche"])
    return {
        "read": len(rows), "cut": len(rows) - kept,
        "cut_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "kept": kept, "mapped": mapped, "unmapped": kept - mapped, "niches": len(nset),
        "checked": len(b["candidates"]), "survived": len(b["top"]),
    }


def _refresh(home, cfg, con, crawl_stats=None, now=None) -> dict:
    """Gate + map + score + sim + export. Shared by run, remap and verdict."""
    now = now or _now(con)
    cuts = filters.apply(con, cfg)
    tax = niches.load(home)
    m = niches.apply(con, tax)
    b = score.board(con, cfg, now)
    s = sim.run(con, cfg, now)
    stats = {"pipeline": _pipeline(con, b), "crawl": crawl_stats or {}, "cuts_all": cuts,
             "mapped_all": m["mapped"], "unmapped_all": m["unmapped"]}
    db.set_meta(con, "last_run", stats)
    names = niches.names(tax)
    data = export.build(con, cfg, names, stats, s, now)
    export.write(home, data)
    md, _ = brief.build(con, cfg, names, stats, s, now)
    (home / "out" / "brief.md").write_text(md)
    return {"stats": stats, "board": b, "sim": s, "names": names}


def cmd_doctor(args):
    home, cfg, con = _open(args)
    checks = {"version": __version__, "python": sys.version.split()[0], "home": str(home)}
    checks["python_ok"] = sys.version_info >= (3, 9)
    try:
        (home / ".write_test").write_text("ok")
        (home / ".write_test").unlink()
        checks["home_writable"] = True
    except OSError as e:
        checks["home_writable"] = False
        checks["home_error"] = str(e)
    checks["workspace_durable"] = str(home).startswith("/workspace")
    checks["niches_in_taxonomy"] = len(niches.load(home))
    checks["jobs_in_db"] = con.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    if not args.offline:
        try:
            data = freelancer.fetch_json(freelancer.page_url(0, limit=1), retries=1, timeout=20)
            n = len((data.get("result") or {}).get("projects") or [])
            checks["freelancer_api"] = "ok" if n else "empty answer"
        except Exception as e:  # noqa: BLE001 - report anything to the bot
            checks["freelancer_api"] = f"failed: {e}"
            checks["hint"] = ("The cloud IP may be blocked. Try routing traffic through the desktop "
                              "(Grok Bot network setting), or collect jobs with the browser and use "
                              "`radar ingest`.")
    checks["ok"] = bool(checks["python_ok"] and checks["home_writable"]
                        and (args.offline or checks.get("freelancer_api") == "ok"))
    print(json.dumps(checks, indent=1))
    return 0 if checks["ok"] else 1


def cmd_run(args):
    home, cfg, con = _open(args)
    if args.max:
        cfg["crawl_max_projects"] = args.max
    t0 = int(time.time())
    crawl_stats = {}
    if not args.no_crawl:
        db.log(con, "scout", "routine woke up · crawling freelancer.com")

        def on_page(q, offset, n):
            if offset % 1000 == 0:
                print(f"  scout: {q or 'all'} offset {offset} · {n} jobs", file=sys.stderr)

        jobs = freelancer.crawl(cfg, on_page=on_page)
        new, upd = db.upsert_jobs(con, jobs, t0)
        crawl_stats = {"fetched": len(jobs), "new": new, "updated": upd}
        db.log(con, "scout", f"read {len(jobs):,} open jobs · {new:,} new since last run")
    r = _refresh(home, cfg, con, crawl_stats)
    p = r["stats"]["pipeline"]
    top = ", ".join(r["names"].get(n, n) for n in r["board"]["top"]) or "none yet"
    db.log(con, "gate", f"cut {p['cut']:,} · " + ", ".join(f"{k} {v}" for k, v in list(p["cut_reasons"].items())[:4]))
    db.log(con, "mapper", f"mapped {p['mapped']:,} jobs into {p['niches']} niches · {p['unmapped']:,} unmapped")
    db.log(con, "scout", f"top now: {top}")
    con.execute("INSERT INTO runs(started, finished, stats) VALUES(?,?,?)",
                (t0, int(time.time()), json.dumps(r["stats"])))
    con.commit()
    # export again so the log lines above are in the dashboard
    export.write(home, export.build(con, cfg, r["names"], r["stats"], r["sim"], _now(con)))
    q = score.queue(con, cfg, _now(con))
    print(json.dumps({"pipeline": p, "crawl": crawl_stats, "top": r["board"]["top"],
                      "skeptic_queue": [s["id"] for s in q],
                      "dashboard": str(home / "out" / "dashboard.html"),
                      "brief": str(home / "out" / "brief.md")}, indent=1))
    return 0


def cmd_remap(args):
    home, cfg, con = _open(args)
    r = _refresh(home, cfg, con)
    print(json.dumps({"pipeline": r["stats"]["pipeline"], "top": r["board"]["top"]}, indent=1))
    return 0


def cmd_queue(args):
    home, cfg, con = _open(args)
    names = niches.names(niches.load(home))
    out = []
    now = _now(con)
    for s in score.queue(con, cfg, now):
        name = names.get(s["id"], s["id"])
        out.append({
            "niche": s["id"], "name": name,
            "jobs_week": s["jobs_week"], "median_value": s["median_value"],
            "median_bids": s["median_bids"], "potential_month": s["potential_month"],
            "previous_verdict": s["verdict"],
            "sample_jobs": score.samples(con, s["id"], 5, now),
            "check": [f"{name} on Fiverr: how many sellers, what prices",
                      f"{name} on Upwork: how many proposals per job",
                      f"{name} complaints or requests on Reddit / X in the last 30 days"],
        })
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def cmd_verdict(args):
    home, cfg, con = _open(args)
    r = score.add_verdict(con, cfg, args.niche, args.verdict, args.note or "", args.market or "",
                          args.evidence or [], now=_now(con))
    db.log(con, "skeptic", f"{args.niche}: {args.verdict}" + (f" · {args.note}" if args.note else ""))
    _refresh(home, cfg, con)
    print(json.dumps(r, indent=1))
    return 0


def cmd_brief(args):
    home, cfg, con = _open(args)
    tax = niches.load(home)
    stats = db.get_meta(con, "last_run", {})
    s = sim.run(con, cfg, _now(con))
    md, data = brief.build(con, cfg, niches.names(tax), stats, s, _now(con))
    (home / "out" / "brief.md").write_text(md)
    print(json.dumps(data, indent=1, ensure_ascii=False, default=str) if args.json else md)
    return 0


def cmd_weekly(args):
    home, cfg, con = _open(args)
    now = _now(con)
    md = brief.weekly(con, cfg, niches.names(niches.load(home)), sim.run(con, cfg, now), now)
    (home / "out" / "weekly.md").write_text(md)
    print(md)
    return 0


def cmd_board(args):
    home, cfg, con = _open(args)
    names = niches.names(niches.load(home))
    b = score.board(con, cfg, _now(con))
    if args.json:
        print(json.dumps(b, indent=1, default=str))
        return 0
    print(f"typical job: {b['typical_bids']:.0f} bids · window {b['window_days']} days")
    print(f"{'niche':34} {'jobs/wk':>7} {'median$':>8} {'bids':>5} {'pot/mo':>8} {'trend':>6}  verdict")
    for s in b["niches"][: args.n]:
        t = "-" if s["trend"] is None else f"{s['trend'] * 100:+.0f}%"
        mark = "*" if s["eligible"] else " "
        print(f"{mark}{names.get(s['id'], s['id'])[:33]:33} {s['jobs_week']:7.0f} {s['median_value']:8.0f} "
              f"{s['median_bids']:5.0f} {s['potential_month']:8.0f} {t:>6}  {s['verdict'] or ''}")
    return 0


def cmd_unmapped(args):
    home, cfg, con = _open(args)
    print(json.dumps(niches.unmapped_sample(con, args.sample), indent=1, ensure_ascii=False))
    return 0


def cmd_taxonomy(args):
    home, cfg, con = _open(args)
    if args.action == "list":
        for n in niches.load(home):
            print(f"{n['id']:22} {n['name']:40} {', '.join(n['keywords'][:4])}")
        return 0
    entry = niches.add(home, args.id, args.name or args.id, (args.keywords or "").split(","),
                       (args.skills or "").split(","))
    db.log(con, "mapper", f"new niche: {entry['name']}")
    r = _refresh(home, cfg, con)
    count = r["stats"]["mapped_all"]
    print(json.dumps({"added": entry, "mapped_all": count, "unmapped_all": r["stats"]["unmapped_all"]}, indent=1))
    return 0


def cmd_ingest(args):
    home, cfg, con = _open(args)
    jobs, errors = inbox.read(Path(args.file), args.source)
    new, upd = db.upsert_jobs(con, jobs)
    db.log(con, "scout", f"ingested {len(jobs)} jobs from {args.source} ({new} new)")
    _refresh(home, cfg, con)
    print(json.dumps({"read": len(jobs), "new": new, "updated": upd, "errors": errors[:20]}, indent=1))
    return 0 if not errors else 2


def cmd_log(args):
    home, cfg, con = _open(args)
    db.log(con, args.agent.lower(), " ".join(args.message))
    return 0


def cmd_export(args):
    home, cfg, con = _open(args)
    r = _refresh(home, cfg, con)
    print(json.dumps({"data": str(home / "out" / "data.json"),
                      "dashboard": str(home / "out" / "dashboard.html"),
                      "top": r["board"]["top"]}, indent=1))
    return 0


def cmd_sim(args):
    home, cfg, con = _open(args)
    s = sim.run(con, cfg, _now(con))
    print(json.dumps({"days": len(s["days"]), "radar": s["radar"][-1:] , "random": s["random"][-1:],
                      "wins": s["wins"][-10:]}, indent=1))
    return 0


def cmd_demo(args):
    from . import demo
    home, cfg, con = _open(args)
    if con.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] and not db.get_meta(con, "demo", False):
        print(f"refusing: {home} already holds real data. Use --home with an empty folder.", file=sys.stderr)
        return 1
    demo.fill(con, cfg, seed=args.seed)
    r = _refresh(home, cfg, con, {"fetched": r_n(con), "new": r_n(con), "updated": 0}, now=demo.NOW)
    demo.verdicts_and_log(con, cfg, r["board"], r["names"])
    r = _refresh(home, cfg, con, {"fetched": r_n(con), "new": r_n(con), "updated": 0}, now=demo.NOW)
    con.execute("INSERT INTO runs(started, finished, stats) VALUES(?,?,?)",
                (demo.NOW - 400, demo.NOW, json.dumps(r["stats"])))
    con.commit()
    export.write(home, export.build(con, cfg, r["names"], r["stats"], r["sim"], demo.NOW))
    print(json.dumps({"home": str(home), "pipeline": r["stats"]["pipeline"], "top": r["board"]["top"]}, indent=1))
    return 0


def r_n(con):
    return con.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radar", description="Money Radar: 24/7 niche hunter for a Grok Bot team")
    p.add_argument("--home", help="data folder (default: $RADAR_HOME or /workspace/money-radar/data)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor"); s.add_argument("--offline", action="store_true"); s.set_defaults(f=cmd_doctor)
    s = sub.add_parser("run"); s.add_argument("--no-crawl", action="store_true"); s.add_argument("--max", type=int)
    s.set_defaults(f=cmd_run)
    sub.add_parser("remap").set_defaults(f=cmd_remap)
    sub.add_parser("queue").set_defaults(f=cmd_queue)
    s = sub.add_parser("verdict")
    s.add_argument("niche"); s.add_argument("--verdict", required=True, choices=["real", "crowded", "unclear"])
    s.add_argument("--note"); s.add_argument("--market"); s.add_argument("--evidence", nargs="*")
    s.set_defaults(f=cmd_verdict)
    s = sub.add_parser("brief"); s.add_argument("--json", action="store_true"); s.set_defaults(f=cmd_brief)
    sub.add_parser("weekly").set_defaults(f=cmd_weekly)
    s = sub.add_parser("board"); s.add_argument("--json", action="store_true"); s.add_argument("-n", type=int, default=25)
    s.set_defaults(f=cmd_board)
    s = sub.add_parser("unmapped"); s.add_argument("--sample", type=int, default=300); s.set_defaults(f=cmd_unmapped)
    s = sub.add_parser("taxonomy"); s.add_argument("action", choices=["list", "add"])
    s.add_argument("--id"); s.add_argument("--name"); s.add_argument("--keywords"); s.add_argument("--skills")
    s.set_defaults(f=cmd_taxonomy)
    s = sub.add_parser("ingest"); s.add_argument("file"); s.add_argument("--source", required=True)
    s.set_defaults(f=cmd_ingest)
    s = sub.add_parser("log"); s.add_argument("agent"); s.add_argument("message", nargs="+"); s.set_defaults(f=cmd_log)
    sub.add_parser("export").set_defaults(f=cmd_export)
    sub.add_parser("sim").set_defaults(f=cmd_sim)
    s = sub.add_parser("demo"); s.add_argument("--seed", type=int, default=11); s.set_defaults(f=cmd_demo)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "taxonomy" and args.action == "add" and not (args.id and args.keywords):
        print("taxonomy add needs --id and --keywords", file=sys.stderr)
        return 2
    try:
        return args.f(args) or 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
