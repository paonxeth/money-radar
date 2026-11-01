"""Synthetic market for trying the dashboard without a crawl. DEMO DATA.

Never mixed with real data: `radar demo` refuses to run in a folder that
already holds real jobs, and everything it writes is flagged demo=true.
"""

from __future__ import annotations

import random

import json
from pathlib import Path

from . import db, score

SEED = {n["id"]: n for n in json.loads(Path(__file__).with_name("taxonomy_seed.json").read_text())}

NOW = 1793512800  # 2026-11-01 06:00 UTC, fixed so demo renders are reproducible
DAY = 86400

# id: (jobs per day, median USD, bids mean, type)
MARKET = {
    "chrome-extension": (11, 480, 8, "fixed"), "n8n-automation": (15, 320, 7, "fixed"),
    "ai-chatbot": (24, 600, 19, "fixed"), "tiktok-shop": (9, 260, 6, "fixed"),
    "notion-setup": (8, 190, 4, "fixed"), "excel-dashboard": (21, 170, 9, "fixed"),
    "telegram-bot": (10, 340, 12, "fixed"), "web-scraping": (24, 200, 16, "fixed"),
    "shopify-store": (20, 400, 22, "fixed"), "wordpress-fix": (34, 250, 25, "fixed"),
    "logo-brand": (38, 90, 45, "fixed"), "mobile-app-fix": (24, 1200, 30, "fixed"),
    "web-app": (28, 1500, 28, "fixed"), "data-entry": (36, 70, 40, "fixed"),
    "video-shorts": (24, 150, 20, "fixed"), "3d-modeling": (17, 230, 13, "fixed"),
    "seo-audit": (24, 150, 25, "fixed"), "lead-lists": (14, 120, 15, "fixed"),
    "ghostwriting": (17, 250, 20, "fixed"), "smart-contract": (11, 900, 17, "fixed"),
    "trading-bot": (9, 600, 10, "fixed"), "ui-design": (24, 300, 30, "fixed"),
    "power-bi": (7, 300, 8, "fixed"), "airtable": (4, 280, 5, "fixed"),
    "amazon-listing": (12, 150, 18, "fixed"), "translation": (15, 100, 25, "fixed"),
    "python-script": (19, 150, 20, "fixed"), "paid-ads": (12, 300, 22, "fixed"),
    "email-marketing": (9, 350, 12, "fixed"), "pitch-deck": (9, 250, 17, "fixed"),
    "cad-drafting": (12, 200, 14, "fixed"), "voice-over": (6, 120, 16, "fixed"),
    "resume": (6, 60, 15, "fixed"), "bookkeeping": (7, 22, 14, "hourly"),
    "motion-graphics": (9, 350, 15, "fixed"), "social-graphics": (15, 60, 30, "fixed"),
    "virtual-assistant": (12, 10, 35, "hourly"), "thumbnails": (8, 40, 25, "fixed"),
    "landing-page": (15, 300, 22, "fixed"), "discord-bot": (5, 250, 9, "fixed"),
}
HEATING = {"n8n-automation": 1.45, "ai-chatbot": 1.25, "tiktok-shop": 1.2, "logo-brand": 0.85, "data-entry": 0.8}

TITLES = {
    "chrome-extension": ["Chrome extension to export {x} to CSV", "Build a browser extension for {x}",
                         "Fix my Chrome extension after Manifest V3", "Chrome extension that autofills {x}"],
    "n8n-automation": ["n8n workflow: {x} to Google Sheets", "Zapier automation for {x}",
                       "Make.com scenario to sync {x}", "Automate {x} with n8n and OpenAI"],
    "ai-chatbot": ["AI chatbot for {x} website", "Build an AI agent that answers {x}",
                   "ChatGPT integration for {x}", "RAG chatbot over our {x} docs"],
    "tiktok-shop": ["Set up our TikTok Shop for {x}", "TikTok Shop listings and affiliate setup",
                    "TikTok Shop account manager for {x} brand"],
    "notion-setup": ["Notion workspace for {x}", "Notion CRM template for {x}", "Organize our {x} in Notion"],
    "excel-dashboard": ["Excel dashboard for {x}", "Google Sheets tracker for {x}", "Excel VBA macro to merge {x}"],
    "telegram-bot": ["Telegram bot for {x} alerts", "Telegram mini app for {x}", "Telegram bot that sells {x}"],
    "trading-bot": ["MT5 expert advisor for {x}", "TradingView Pine Script strategy for {x}", "Binance bot for {x}"],
    "airtable": ["Airtable base for {x}", "Airtable automation for {x} pipeline"],
    "power-bi": ["Power BI report for {x}", "Looker Studio dashboard for {x}"],
    "email-marketing": ["Klaviyo flows for {x} store", "Mailchimp newsletter for {x}"],
    "3d-modeling": ["3D model of {x} for printing", "Blender product render of {x}"],
}
X = ["orders", "leads", "invoices", "Amazon reviews", "real estate listings", "Shopify products", "Etsy sales",
     "LinkedIn posts", "crypto prices", "support tickets", "job posts", "Instagram DMs", "customer emails",
     "restaurant menus", "clinic bookings", "podcast episodes", "YouTube comments", "inventory"]
UNMAPPED = ["Help with my project", "Small task for an expert", "Need help ASAP", "Quick fix needed",
            "Looking for a reliable freelancer", "Project for {x}", "Ongoing work for {x}", "Simple job for {x}"]
JUNK = [
    ("Unpaid test task for designers", "spam", 50), ("Commission only sales rep", "spam", 100),
    ("Rent your verified account", "spam", 40), ("Plumber needed in Sydney", "local", 200),
    ("Full-time position: office assistant", "not a gig", 800), ("Write 1 product description", "low", 8),
    ("Like and share my posts", "low", 5), ("Logo for my channel", "low", 12),
]


def fill(con, cfg, seed: int = 11) -> None:
    rnd = random.Random(seed)
    db.set_meta(con, "demo", True)
    db.set_meta(con, "demo_now", NOW)
    jobs = []
    n = 0
    start = NOW - 14 * DAY
    for day in range(14):
        d0 = start + day * DAY
        recent = day >= 7
        for nid, (per_day, med, bids_mean, typ) in MARKET.items():
            mult = HEATING.get(nid, 1.0) if recent else 1.0
            for _ in range(max(0, int(rnd.gauss(per_day * mult, per_day * 0.2)))):
                n += 1
                kw = rnd.choice(SEED[nid]["keywords"][:2])
                title = rnd.choice(TITLES.get(nid, ["{name} needed", "Looking for {name} expert",
                                                     "{name} for my business", "{name} for {x}"]))
                title = title.format(x=rnd.choice(X), name=kw[0].upper() + kw[1:])
                v = max(10.0, rnd.lognormvariate(0, 0.45) * med)
                lo, hi = round(v * 0.7, -1) or 10, round(v * 1.3, -1) or 20
                if typ == "hourly":
                    lo, hi = round(v * 0.8), round(v * 1.2)
                jobs.append(_job(n, title, SEED[nid]["skills"][:1], typ, lo, hi,
                                 max(0, int(rnd.gauss(bids_mean, bids_mean * 0.45))), d0 + rnd.randint(0, DAY - 1)))
        for _ in range(int(rnd.gauss(55, 8))):
            n += 1
            title = rnd.choice(UNMAPPED).format(x=rnd.choice(X))
            v = rnd.lognormvariate(0, 0.6) * 150
            jobs.append(_job(n, f"{title} #{n % 97}", ["Freelance"], "fixed", round(v * .7), round(v * 1.3),
                             max(0, int(rnd.gauss(14, 6))), d0 + rnd.randint(0, DAY - 1)))
        for _ in range(int(rnd.gauss(70, 10))):
            n += 1
            title, kind, price = rnd.choice(JUNK)
            j = _job(n, f"{title} {n}", ["Other"], "fixed", price * 0.8, price * 1.2,
                     rnd.randint(3, 60), d0 + rnd.randint(0, DAY - 1))
            if kind == "local":
                j["local"], j["category"] = True, "Local Jobs & Services"
            jobs.append(j)
        # a few reposts of the same title
        for _ in range(int(rnd.gauss(12, 3))):
            if jobs:
                twin = dict(rnd.choice(jobs[-300:]))
                n += 1
                twin["id"] = f"demo:{n}"
                jobs.append(twin)
    db.upsert_jobs(con, jobs, NOW)
    for j in jobs:
        open_now = j["posted_at"] >= NOW - 7 * DAY
        last = NOW if open_now else min(NOW - DAY, j["posted_at"] + rnd.randint(2, 7) * DAY)
        con.execute("UPDATE jobs SET first_seen=?, last_seen=? WHERE id=?", (j["posted_at"] + 3600, last, j["id"]))
    con.commit()


def _job(n, title, skills, typ, lo, hi, bids, posted):
    return {"id": f"demo:{n}", "source": "freelancer", "owner": f"c{n}", "title": title, "skills": skills, "category": None,
            "type": typ, "budget_min": float(lo), "budget_max": float(hi), "bids": bids, "bid_avg": None,
            "url": f"https://www.freelancer.com/projects/demo-{n}", "language": "en", "local": False,
            "posted_at": posted}


VERDICTS = {
    "trading-bot": ("crowded", "Fiverr: thousands of MT4/MT5 EA gigs from $30", "$30-150 on Fiverr"),
    "excel-dashboard": ("crowded", "looks empty here, packed on Upwork and Fiverr", "$40-120 on Fiverr"),
    "chrome-extension": ("real", "few sellers, MV3 rewrites keep coming", "$300-900 on Upwork"),
    "n8n-automation": ("real", "demand growing faster than sellers", "$250-700 on Upwork"),
    "notion-setup": ("real", "small jobs but almost no competition", "$150-400 on Fiverr"),
    "tiktok-shop": ("real", "brands ask for setup + affiliate outreach", "$200-500 on Upwork"),
    "airtable": ("unclear", "real buyers, very small volume", "$200-500 on Upwork"),
}


def verdicts_and_log(con, cfg, board, names) -> None:
    t = NOW - 5400
    db.log(con, "scout", "routine woke up · crawling freelancer.com", t)
    for i in range(1, 6):
        db.log(con, "scout", f"page {i * 10}/52 · {i * 1000:,} jobs read", t + i * 40)
    db.log(con, "gate", "plain python · rules only · $0", t + 240)
    db.log(con, "mapper", "assigning jobs to 40 niches by title + skills", t + 260)
    t = NOW - 3600
    db.log(con, "skeptic", f"queue: {len(board['candidates'])} niches · checking new or changed only", t)
    for k, nid in enumerate(board["candidates"]):
        v, note, market = VERDICTS.get(nid, ("unclear", "mixed signals, re-check next week", ""))
        db.log(con, "skeptic", f"searching Fiverr / Upwork / Reddit for {names.get(nid, nid)}", t + 60 + k * 90)
        score.add_verdict(con, cfg, nid, v, note, market, [], now=t + 100 + k * 90)
        db.log(con, "skeptic", f"{names.get(nid, nid)}: {v} · {note}", t + 120 + k * 90)
    db.log(con, "analyst", "simulated freelancer replayed · radar vs random", NOW - 4000)
    db.log(con, "briefer", "morning brief ready · 3 niches · sent to phone", NOW - 300)
