"""Automated, non-interactive BestBuy.ca search & offer verifier (Good & Fair)."""
import argparse
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from agent_support import read_rows, search_model_name, write_rows
from bestbuy_support import canonical_url, title_check, verify_snapshot
from fetcher import fetch_html, invalidate_mapping, load_url_cache, record_verified_mapping

folder = Path(__file__).resolve().parent

OFFER_FIELDS = [
    "checked_at", "shopify_variant_id", "model", "storage", "color",
    "condition", "store", "product_title", "price_cad", "seller",
    "source_url", "status", "reason",
]


def search_bestbuy_urls(target):
    model = search_model_name(target["product_title"])
    query = f"Refurbished {target['condition']} {model} {target['storage']} {target['color']} Unlocked"
    search_url = f"https://www.bestbuy.ca/en-ca/search?search={quote_plus(query)}"
    html = fetch_html(search_url)
    soup = BeautifulSoup(html, "lxml")

    urls = []
    seen = set()
    for a in soup.select('a[href*="/en-ca/product/"]'):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full_url = href if href.startswith("http") else f"https://www.bestbuy.ca{href}"
        try:
            clean = canonical_url(full_url)
        except ValueError:
            continue
        if clean in seen:
            continue
        card = a.find_parent(["li", "article", "div"])
        card_text = " ".join((card.get_text(" ", strip=True) if card else a.get_text(" ", strip=True)).split())
        try:
            title_check(card_text, target)
        except ValueError:
            continue
        seen.add(clean)
        urls.append(clean)
        if len(urls) >= 2:
            break
    return urls


def build_snapshot_from_html(html, url):
    soup = BeautifulSoup(html, "lxml")
    summary = soup.select_one('[data-automation="layout-product-summary"]') or soup
    offer_details = soup.select_one('[data-automation="layout-product-offer-details"]') or soup

    h1 = summary.select_one("h1")
    if not h1:
        raise ValueError("Best Buy product title missing (blocked or page changed).")
    title = " ".join(h1.get_text(" ", strip=True).split())

    sku_el = summary.select_one('[data-automation="SKU_ID"]')
    sku_text = sku_el.get_text(" ", strip=True) if sku_el else soup.get_text(" ", strip=True)

    seller_el = summary.select_one('[data-testid="SoldByBestBuyOrMarketplaceSellerTestId"]')
    seller_text = seller_el.get_text("\n", strip=True) if seller_el else ""
    if not seller_text:
        m = re.search(r"(Sold and shipped by[^\n|]+)", soup.get_text("\n", strip=True), re.I)
        seller_text = m.group(1) if m else ""

    price_el = offer_details.select_one('[class*="pricingContainer_"]')
    offer_text = price_el.get_text("\n", strip=True) if price_el else offer_details.get_text("\n", strip=True)

    ship_el = offer_details.select_one('[data-testid="shipping-container"]')
    avail_text = ship_el.get_text(" ", strip=True) if ship_el else offer_details.get_text(" ", strip=True)

    cart_btn = offer_details.select_one('button[data-automation="add-to-cart-button"]')
    cart_enabled = bool(
        cart_btn
        and not cart_btn.has_attr("disabled")
        and cart_btn.get("aria-disabled") != "true"
    )

    overview_el = soup.select_one('[data-automation="layout-product-overview"]')
    evidence_text = overview_el.get_text("\n", strip=True) if overview_el else ""

    specs_btn = soup.select_one('[data-automation="specifications-expandable-button"]')
    specs_text = specs_btn.parent.get_text("\n", strip=True) if specs_btn and specs_btn.parent else soup.get_text("\n", strip=True)

    return {
        "url": url,
        "title": title,
        "sku_text": sku_text,
        "seller_text": seller_text,
        "offer_text": offer_text,
        "availability_text": avail_text,
        "cart_enabled": cart_enabled,
        "evidence_text": evidence_text,
        "specs_text": specs_text,
    }


def process_targets(targets):
    cache = load_url_cache()
    records = []
    for target in targets:
        vid = target["variant_id"]
        rec = {
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "shopify_variant_id": vid,
            "model": target["product_title"],
            "storage": target["storage"],
            "color": target["color"],
            "condition": target["condition"],
            "store": "BestBuy.ca",
            "product_title": "",
            "price_cad": "",
            "seller": "",
            "source_url": "",
            "status": "HOLD",
            "reason": "",
        }
        try:
            candidate_urls = []
            cached = cache.get(vid)
            if cached and cached.get("store") == "BestBuy.ca" and cached.get("url"):
                candidate_urls.append(cached["url"])

            verified = False
            last_err = "No matching candidate found on BestBuy.ca."
            for attempt_round in range(2):
                if attempt_round == 1:
                    if cached:
                        invalidate_mapping(vid)
                    candidate_urls = [
                        u for u in search_bestbuy_urls(target) if u not in candidate_urls
                    ]
                for url in candidate_urls:
                    try:
                        html = fetch_html(url)
                        snapshot = build_snapshot_from_html(html, url)
                        price, seller = verify_snapshot(snapshot, target, url)
                        rec.update({
                            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "product_title": snapshot["title"],
                            "price_cad": f"{price:.2f}",
                            "seller": seller,
                            "source_url": url,
                            "status": "VERIFIED",
                            "reason": "",
                        })
                        record_verified_mapping(vid, "BestBuy.ca", url, snapshot["title"])
                        print(f"[BESTBUY VERIFIED] {target['product_title']} {target['storage']} "
                              f"{target['color']} ({target['condition']}): ${price:.2f} CAD", flush=True)
                        verified = True
                        break
                    except Exception as exc:
                        rec["source_url"] = url
                        last_err = str(exc).splitlines()[0]
                if verified:
                    break

            if not verified:
                rec["reason"] = last_err
                print(f"[BESTBUY HOLD] {target['product_title']} {target['storage']} "
                      f"{target['color']} ({target['condition']}): {last_err}", flush=True)
        except Exception as exc:
            rec["reason"] = str(exc).splitlines()[0]
            print(f"[BESTBUY ERROR] {vid}: {rec['reason']}", flush=True)

        records.append(rec)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-file", type=Path, default=folder / "batch_targets.csv")
    parser.add_argument("--output", type=Path, default=folder / "bestbuy_verified_offers.csv")
    args = parser.parse_args()

    targets = [
        r for r in read_rows(args.targets_file)
        if r.get("competitor_source") == "BestBuy.ca"
    ]
    write_rows(args.output, [], OFFER_FIELDS)
    if not targets:
        print("No BestBuy.ca targets in batch_targets.csv.")
        return
    records = process_targets(targets)
    write_rows(args.output, records, OFFER_FIELDS)
    print(f"Saved {len(records)} BestBuy.ca records to {args.output.name}")


if __name__ == "__main__":
    main()