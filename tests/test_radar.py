"""Run with: python3 -m unittest discover -s tests"""

import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from radar import config, db, filters, niches, score, sim  # noqa: E402
from radar.sources import freelancer, inbox  # noqa: E402

PAGE = json.loads((ROOT / "tests/fixtures/freelancer_page.json").read_text())
NOW = 1793512800


def fresh_db():
    con = db.connect(Path(":memory:"))
    return con


class Normalize(unittest.TestCase):
    def setUp(self):
        self.jobs = [freelancer.normalize(p) for p in PAGE["result"]["projects"]]

    def test_usd_conversion(self):
        logo = self.jobs[1]
        self.assertEqual(logo["budget_min"], 7.2)   # 600 INR * 0.012
        self.assertEqual(logo["budget_max"], 18.0)
        n8n = self.jobs[2]
        self.assertEqual(n8n["type"], "hourly")
        self.assertAlmostEqual(n8n["budget_max"], 27.0)

    def test_fields(self):
        j = self.jobs[0]
        self.assertEqual(j["id"], "freelancer:40111001")
        self.assertEqual(j["owner"], "9001")
        self.assertEqual(j["skills"], ["JavaScript", "Google Chrome"])
        self.assertEqual(j["category"], "Websites, IT & Software")
        self.assertEqual(j["bids"], 9)
        self.assertTrue(j["url"].startswith("https://www.freelancer.com/projects/javascript/"))

    def test_inverse_rate_guard(self):
        p = copy.deepcopy(PAGE["result"]["projects"][1])
        p["currency"]["exchange_rate"] = 83.0  # units per USD
        self.assertAlmostEqual(freelancer.normalize(p)["budget_min"], round(600 / 83.0, 2), places=1)

    def test_bad_rows(self):
        self.assertIsNone(freelancer.normalize({"id": 1}))
        self.assertIsNotNone(freelancer.normalize({"id": 1, "title": "x"}))


class Crawl(unittest.TestCase):
    def test_paging_wall(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return PAGE  # every page identical, like past the ~4,990 wall

        jobs = freelancer.crawl({"crawl_max_projects": 5000, "crawl_delay_s": 0}, fetch=fetch)
        self.assertEqual(len(jobs), 8)
        self.assertEqual(len(calls), 2)  # second identical page stops it

    def test_api_error(self):
        with self.assertRaises(RuntimeError):
            freelancer.crawl({"crawl_delay_s": 0}, fetch=lambda u: {"status": "error", "message": "nope"})


class Gate(unittest.TestCase):
    def test_reasons(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        db.upsert_jobs(con, [freelancer.normalize(p) for p in PAGE["result"]["projects"]], NOW)
        cuts = filters.apply(con, cfg)
        rows = {r["id"]: r["cut"] for r in con.execute("SELECT id, cut FROM jobs")}
        self.assertIsNone(rows["freelancer:40111001"])
        self.assertEqual(rows["freelancer:40111002"], "low budget")
        self.assertIsNone(rows["freelancer:40111003"])           # hourly $16-27 is fine
        self.assertEqual(rows["freelancer:40111004"], "local")
        self.assertEqual(rows["freelancer:40111005"], "spam")
        self.assertEqual(rows["freelancer:40111006"], "duplicate")  # same client, same title
        self.assertEqual(rows["freelancer:40111007"], "low budget")
        self.assertEqual(rows["freelancer:40111008"], "language")
        self.assertEqual(sum(cuts.values()), 6)

    def test_same_title_other_client_is_kept(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        a = freelancer.normalize(PAGE["result"]["projects"][0])
        b = dict(a, id="freelancer:2", owner="other")
        db.upsert_jobs(con, [a, b], NOW)
        filters.apply(con, cfg)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs WHERE cut IS NULL").fetchone()[0], 2)


class Map(unittest.TestCase):
    def setUp(self):
        self.m = niches.Matcher(json.loads(niches.SEED.read_text()))

    def test_assign(self):
        self.assertEqual(self.m.assign("Chrome extension to export leads", []), "chrome-extension")
        self.assertEqual(self.m.assign("n8n workflow to sync Shopify orders", []), "n8n-automation")
        self.assertEqual(self.m.assign("Convert PDF to Excel", []), "data-entry")
        self.assertEqual(self.m.assign("3D animation of a product", []), "3d-modeling")
        self.assertIsNone(self.m.assign("Help with my project", ["Freelance"]))

    def test_skills_alone_need_two(self):
        self.assertIsNone(self.m.assign("Help with my store", ["Shopify"]))
        self.assertEqual(self.m.assign("Help with my store", ["Shopify", "Shopify Templates"]), "shopify-store")

    def test_add_niche(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            niches.add(home, "cursor-rules", "Cursor / Claude Code setup", ["cursor", "claude code"], [])
            tax = niches.load(home)
            self.assertEqual(tax[0]["id"], "cursor-rules")
            with self.assertRaises(ValueError):
                niches.add(home, "Bad Id", "x", ["k"], [])


def market(con, n_per_niche, now=NOW):
    """Fixture market: chrome = few bids + big budgets, logo = crowded."""
    jobs = []
    k = 0
    for nid, title, budget, bids in (("chrome-extension", "Chrome extension for {i}", 500, 5),
                                     ("logo-brand", "Logo for brand {i}", 100, 40),
                                     ("notion-setup", "Notion setup {i}", 200, 3),
                                     ("wordpress-fix", "WordPress fix {i}", 150, 20)):
        for i in range(n_per_niche):
            k += 1
            jobs.append({"id": f"t:{k}", "source": "t", "owner": f"o{k}", "title": title.format(i=i),
                         "skills": [], "type": "fixed", "budget_min": budget * 0.8, "budget_max": budget * 1.2,
                         "bids": bids, "url": f"https://x/{k}", "language": "en",
                         "posted_at": now - 86400 * (1 + i % 13)})
    db.upsert_jobs(con, jobs, now - 20 * 86400)


class Score(unittest.TestCase):
    def test_potential_formula(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        market(con, 60)
        filters.apply(con, cfg)
        niches.apply(con, json.loads(niches.SEED.read_text()))
        b = score.board(con, cfg, NOW)
        by = {s["id"]: s for s in b["niches"]}
        chrome = by["chrome-extension"]
        # capped at 2 jobs/week x median $500, x 30/7 for a month
        self.assertAlmostEqual(chrome["potential_month"], round(2 * 500 * 30 / 7, 2), places=1)
        self.assertTrue(chrome["eligible"])
        self.assertFalse(by["logo-brand"]["eligible"])   # more bids than the typical job
        self.assertEqual(b["candidates"][0], "chrome-extension")
        self.assertIsNotNone(chrome["trend"])           # we crawled for >7 days in this fixture

    def test_queue_skips_fresh_verdicts(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        market(con, 60)
        filters.apply(con, cfg)
        niches.apply(con, json.loads(niches.SEED.read_text()))
        before = [s["id"] for s in score.queue(con, cfg, NOW)]
        self.assertIn("chrome-extension", before)
        score.add_verdict(con, cfg, "chrome-extension", "real", "ok", "$300+", [], now=NOW)
        after = [s["id"] for s in score.queue(con, cfg, NOW)]
        self.assertNotIn("chrome-extension", after)
        # a verdict older than the TTL gets re-checked
        con.execute("UPDATE verdicts SET ts = ? WHERE niche = 'chrome-extension'", (NOW - 8 * 86400,))
        stale = [s["id"] for s in score.queue(con, cfg, NOW)]
        self.assertIn("chrome-extension", stale)
        with self.assertRaises(ValueError):
            score.add_verdict(con, cfg, "x", "maybe", "", "", [], now=NOW)

    def test_crowded_drops_from_top(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        market(con, 60)
        filters.apply(con, cfg)
        niches.apply(con, json.loads(niches.SEED.read_text()))
        score.add_verdict(con, cfg, "chrome-extension", "crowded", "fiverr full", "", [], now=NOW)
        b = score.board(con, cfg, NOW)
        self.assertNotIn("chrome-extension", b["top"])
        self.assertIn("chrome-extension", b["crowded"])


class Sim(unittest.TestCase):
    def test_deterministic_and_conservative(self):
        con = fresh_db()
        cfg = config.load(Path("/nonexistent.json"))
        market(con, 120)
        filters.apply(con, cfg)
        niches.apply(con, json.loads(niches.SEED.read_text()))
        a = sim.run(con, cfg, NOW)
        b = sim.run(con, cfg, NOW)
        self.assertEqual(a, b)
        self.assertTrue(a["days"])
        self.assertEqual(len(a["days"]), len(a["radar"]))
        # balances never go down
        self.assertEqual(a["radar"], sorted(a["radar"]))
        for w in a["wins"]:
            self.assertLessEqual(w["value"], 600.0)


class Inbox(unittest.TestCase):
    def test_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"title": "Upwork: n8n expert", "url": "https://u/1", "budget_min": 200,
                                "bids": 12, "posted_at": "2026-10-30T10:00:00Z"}) + "\n")
            f.write("{bad json\n")
            f.write(json.dumps({"title": "no url"}) + "\n")
        jobs, errors = inbox.read(Path(f.name), "upwork")
        os.unlink(f.name)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(errors), 2)
        self.assertEqual(jobs[0]["budget_max"], 200.0)
        self.assertTrue(jobs[0]["id"].startswith("upwork:"))


class CLI(unittest.TestCase):
    def run_cli(self, home, *args):
        env = dict(os.environ, RADAR_HOME=str(home))
        return subprocess.run([sys.executable, "-m", "radar", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_demo_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(d, "demo")
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(len(out["top"]), 3)
            for cmd in (["board"], ["brief"], ["queue"], ["unmapped", "--sample", "5"], ["weekly"], ["sim"],
                        ["export"], ["doctor", "--offline"]):
                r = self.run_cli(d, *cmd)
                self.assertEqual(r.returncode, 0, (cmd, r.stderr))
            html = (Path(d) / "out/dashboard.html").read_text()
            self.assertNotIn("/*__RADAR_DATA__*/null", html)
            r = self.run_cli(d, "taxonomy", "add", "--id", "help-generic", "--name", "Generic help",
                             "--keywords", "help with my project")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_cli(d, "verdict", "notion-setup", "--verdict", "real", "--note", "few sellers")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_cli(d, "log", "briefer", "sent", "brief")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_demo_refuses_real_folder(self):
        with tempfile.TemporaryDirectory() as d:
            con = db.connect(Path(d) / "radar.db")
            db.upsert_jobs(con, [freelancer.normalize(PAGE["result"]["projects"][0])])
            con.close()
            r = self.run_cli(d, "demo")
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
