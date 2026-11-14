"""Unblocked Canadian HTML fetcher (Bright Data Web Unlocker / Scrapfly / Playwright fallback)
plus persistent ASIN/Web-Code URL cache.
"""
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

folder = Path(__file__).resolve().parent
load_dotenv(folder / ".env")

SCRAPER_PROVIDER = os.getenv("SCRAPER_PROVIDER", "brightdata").strip().lower()
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "").strip()
SCRAPER_ZONE = os.getenv("SCRAPER_ZONE", "web_unlocker1").strip()

CACHE_PATH = folder / "verified_mappings.json"


def load_url_cache():
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_url_cache(cache):
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(CACHE_PATH)


def record_verified_mapping(variant_id, store, url, product_title):
    cache = load_url_cache()
    cache[variant_id] = {
        "store": store,
        "url": url,
        "product_title": product_title,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    save_url_cache(cache)


def invalidate_mapping(variant_id):
    cache = load_url_cache()
    if variant_id in cache:
        del cache[variant_id]
        save_url_cache(cache)


def fetch_html(url, timeout=90, retries=2):
    """Fetch unblocked Canadian HTML using configured scraping API or Playwright fallback."""
    time.sleep(random.uniform(1.0, 2.5))

    if SCRAPER_API_KEY:
        last_error = None
        for attempt in range(retries):
            try:
                if SCRAPER_PROVIDER == "scrapfly":
                    resp = requests.get(
                        "https://api.scrapfly.io/scrape",
                        params={
                            "key": SCRAPER_API_KEY,
                            "url": url,
                            "country": "ca",
                            "asp": "true",
                            "render_js": "true",
                        },
                        timeout=timeout,
                    )
                    if resp.status_code != 200:
                        raise ValueError(f"Scrapfly HTTP {resp.status_code}")
                    html = resp.json().get("result", {}).get("content", "")
                    if not html:
                        raise ValueError("Scrapfly returned empty HTML.")
                    return html

                # Default: Bright Data Web Unlocker API
                resp = requests.post(
                    "https://api.brightdata.com/request",
                    headers={
                        "Authorization": f"Bearer {SCRAPER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "zone": SCRAPER_ZONE,
                        "url": url,
                        "format": "raw",
                        "country": "ca",
                    },
                    timeout=timeout,
                )
                if resp.status_code != 200:
                    raise ValueError(f"Bright Data HTTP {resp.status_code}: {resp.text[:160]}")
                if not resp.text or "access denied" in resp.text[:1000].casefold():
                    raise ValueError("Target site denied access via unlocker.")
                return resp.text
            except (requests.Timeout, ValueError) as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(3)
        raise ValueError(f"Fetch failed after {retries} attempts: {last_error}")

    # Local Playwright fallback (used only when SCRAPER_API_KEY is not set)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en-CA",
            timezone_id="America/Vancouver",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            html = page.content()
            if "access denied" in page.title().casefold():
                raise ValueError("Local browser blocked (Access Denied). Set SCRAPER_API_KEY in .env.")
            return html
        finally:
            browser.close()