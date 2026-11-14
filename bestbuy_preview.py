"""Supervised Best Buy pilot. Never imports or invokes Shopify price scripts."""
import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from agent_support import read_rows, fresh
from bestbuy_support import canonical_url, verify_snapshot, web_code, condition_grade, GRADES, normalize

FIELDS = ["checked_at", "variant_id", "model", "storage", "color", "condition",
          "store", "product_title", "price_cad", "discount_cad", "proposed_price_cad",
          "seller", "source_url", "offer_selection", "collection_method", "status", "reason"]


def save_snapshot(run, index, target, record, snapshot, checked_at):
    record.update(checked_at=checked_at, source_url=canonical_url(snapshot["url"]),
                  offer_selection="MAIN_DISPLAYED_OFFER")
    fresh(checked_at)
    (run / f"evidence_{index}_{web_code(record['source_url'])}.json").write_text(
        json.dumps({"checked_at": checked_at, "snapshot": snapshot}, indent=2), encoding="utf-8")
    price, seller = verify_snapshot(snapshot, target, record["source_url"])
    record.update(product_title=snapshot["title"], price_cad=f"{price:.2f}", discount_cad="3.00",
                  proposed_price_cad=f"{price - 3:.2f}", seller=seller, status="PREVIEW_ONLY")
    print(f"PREVIEW: {target['color']} {target['condition']} competitor ${price:.2f}; proposed ${price - 3:.2f} CAD")


def import_capture(path, run, target):
    record = dict.fromkeys(FIELDS, "")
    record.update(variant_id=target["variant_id"], model=target["product_title"], storage=target["storage"],
                  color=target["color"], condition=target["condition"], store="BestBuy.ca",
                  collection_method="USER_BROWSER_CAPTURE", status="HOLD")
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError("Capture file is too large.")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("schema_version") != 1 or data.get("source") != "BESTBUY_VISIBLE_MAIN_OFFER":
            raise ValueError("Not a supported Best Buy browser capture.")
        save_snapshot(run, 0, target, record, data["snapshot"], data["checked_at"])
    except Exception as error:
        record["reason"] = str(error)
        print("HOLD:", error)
    write_report(run / "bestbuy_report.csv", [record])
    print("Report:", run / "bestbuy_report.csv")
    print("Preview only. No Shopify access or price changes.")
    return 0 if record["status"] == "PREVIEW_ONLY" else 1


def catalogue(path, model, condition):
    condition = condition_grade(condition)
    selected = {}
    for row in read_rows(path):
        if row.get("product_title") != model or normalize(row.get("condition", "")) != normalize(condition):
            continue
        if not re.fullmatch(r"gid://shopify/ProductVariant/\d+", row.get("variant_id", "")):
            raise ValueError("Invalid variant ID in the selected condition.")
        if not row.get("storage") or not row.get("color"):
            raise ValueError("Selected target is missing storage or colour.")
        row = dict(row, condition=condition)
        old = selected.get(row["variant_id"])
        if old and any(old[k] != row[k] for k in ("product_title", "storage", "color", "condition")):
            raise ValueError("Conflicting metadata for the same variant ID.")
        selected[row["variant_id"]] = row
    return list(selected.values())


def ensure_access(page):
    if "access denied" in page.title().casefold():
        raise ValueError("Best Buy denied browser access. Stop this pilot; no offer was verified.")


def visible_text(page, selector):
    nodes = page.locator(selector)
    visible = [nodes.nth(i) for i in range(nodes.count()) if nodes.nth(i).is_visible()]
    if len(visible) != 1:
        raise ValueError("Missing or ambiguous visible product section: " + selector)
    return visible[0].inner_text()


def capture(page):
    # Read only the primary product sections, excluding recommendations,
    # sticky footer duplicates and hidden alternate offers.
    for selector in ('[data-automation="specifications-expandable-button"]',
                     '[data-automation="aboutThisProduct-expandable-button"]'):
        button = page.locator(selector)
        if button.count() == 1 and button.is_visible() and button.get_attribute("aria-expanded") == "false":
            button.click()
    specs = page.locator('[data-automation="specifications-expandable-button"]')
    if specs.count() != 1 or not specs.is_visible():
        raise ValueError("Visible specifications section is missing or ambiguous.")
    delivery_selector = '[data-automation="layout-product-offer-details"] [data-automation="delivery-tab"]'
    actions = page.locator(delivery_selector)
    if actions.count() != 1 or not actions.is_visible():
        raise ValueError("Primary availability section is missing or ambiguous.")
    buttons = actions.locator('button[data-automation="add-to-cart-button"]:not(section.modal button, [role="dialog"] button)')
    visible_buttons = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
    if len(visible_buttons) != 1:
        raise ValueError("Missing or ambiguous main delivery Add to Cart button.")
    return {
        "url": page.url,
        "title": visible_text(page, '[data-automation="layout-product-summary"] h1'),
        "sku_text": visible_text(page, '[data-automation="layout-product-summary"] [data-automation="SKU_ID"]'),
        "seller_text": visible_text(page, '[data-automation="layout-product-summary"] [data-testid="SoldByBestBuyOrMarketplaceSellerTestId"]'),
        "offer_text": visible_text(page, '[data-automation="layout-product-offer-details"] [class*="pricingContainer_"]'),
        "availability_text": visible_text(page, delivery_selector + ' [data-testid="shipping-container"]'),
        "cart_enabled": visible_buttons[0].is_enabled() and visible_buttons[0].get_attribute("aria-disabled") != "true",
        "evidence_text": visible_text(page, '[data-automation="layout-product-overview"]'),
        "specs_text": specs.locator("xpath=..").inner_text(),
    }


def write_report(path, records):
    # Each run has a new directory: no replacement of an open Excel CSV.
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--condition", help="Exact Tech Genius grade, e.g. Good or Very Good. Excellent uses Amazon.")
    parser.add_argument("--list-conditions", action="store_true", help="List non-Excellent catalogue grades offline.")
    parser.add_argument("--catalogue", type=Path, help="Variant CSV; defaults to competitor_searches.csv beside this script.")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--list-targets", action="store_true")
    parser.add_argument("--capture-file", type=Path,
                        help="Verify a fresh capture downloaded by the regular-Chrome bookmark; no automated browser access.")
    args = parser.parse_args(argv)
    if args.start < 0 or not 1 <= args.limit <= 5:
        parser.error("Use --start >= 0 and --limit between 1 and 5.")
    folder = Path(__file__).resolve().parent
    path = args.catalogue or folder / "competitor_searches.csv"
    if not path.is_file():
        parser.error("Catalogue CSV is missing: " + str(path))
    if args.list_conditions:
        rows = read_rows(path)
        for grade in GRADES:
            count = len({r.get("variant_id") for r in rows if r.get("product_title") == args.model
                         and normalize(r.get("condition", "")) == normalize(grade)})
            if count:
                print(f"{grade}: {count} targets")
        if not any(r.get("product_title") == args.model and
                   normalize(r.get("condition", "")) in {normalize(g) for g in GRADES} for r in rows):
            print("No non-Excellent targets found. Supply a catalogue containing the actual condition-specific variant IDs.")
        print("Offline check only. Excellent targets stay with Amazon.")
        return 0
    if not args.condition:
        parser.error("--condition is required. Use --list-conditions to inspect available targets.")
    try:
        targets = catalogue(path, args.model, args.condition)[args.start:args.start + args.limit]
    except ValueError as error:
        parser.error(str(error))
    if not targets:
        parser.error("No targets for that model and condition. Do not reuse an Excellent variant ID.")
    for target in targets:
        print(f"{target['product_title']} {target['storage']} {target['color']} {target['condition']} | {target['variant_id']}")
    if args.list_targets:
        return 0
    if args.capture_file and args.limit != 1:
        parser.error("--capture-file requires --limit 1.")
    run = folder / "bestbuy_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run.mkdir(parents=True, exist_ok=False)
    if args.capture_file:
        return import_capture(args.capture_file, run, targets[0])
    from playwright.sync_api import sync_playwright
    records = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            for index, target in enumerate(targets):
                record = dict.fromkeys(FIELDS, "")
                record.update(variant_id=target["variant_id"], model=target["product_title"],
                              storage=target["storage"], color=target["color"],
                              condition=target["condition"], store="BestBuy.ca", status="HOLD")
                record.update(offer_selection="MAIN_DISPLAYED_OFFER", collection_method="AGENT_BROWSER")
                records.append(record)
                try:
                    query = f"refurbished {target['product_title']} {target['storage']} {target['color']}"
                    page.goto("https://www.bestbuy.ca/en-ca/search?search=" + quote_plus(query),
                              wait_until="domcontentloaded", timeout=30000)
                    ensure_access(page)
                    print(f"\nSelect a matching Refurbished {target['condition']} product in this browser.")
                    value = input("Paste its Best Buy product URL here, n for no match, or q to stop: ").strip()
                    if value.casefold() == "q":
                        raise KeyboardInterrupt
                    if value.casefold() == "n":
                        record.update(status="NO_CANDIDATE", reason="No matching product selected.")
                        continue
                    url = canonical_url(value)
                    record["source_url"] = url
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    input("Complete any site prompt. Expand Specifications and About This Product. "
                          "When price and seller are visible, press Enter here: ")
                    ensure_access(page)
                    snapshot = capture(page)
                    if web_code(page.url) != web_code(url):
                        raise ValueError("Page changed to a different Best Buy product.")
                    save_snapshot(run, index, target, record, snapshot,
                                  datetime.now().astimezone().isoformat())
                except KeyboardInterrupt:
                    record["reason"] = "Stopped by user."
                    for remaining in targets[index + 1:]:
                        pending = dict.fromkeys(FIELDS, "")
                        pending.update(variant_id=remaining["variant_id"], model=remaining["product_title"],
                                       storage=remaining["storage"], color=remaining["color"],
                                       condition=remaining["condition"], store="BestBuy.ca",
                                       status="NOT_PROCESSED", reason="Stopped by user.")
                        records.append(pending)
                    break
                except Exception as error:
                    record["status"] = "HOLD" if isinstance(error, ValueError) else "ERROR"
                    record["reason"] = str(error)
                    print(record["status"] + ":", error)
                finally:
                    write_report(run / "bestbuy_report.csv", records)
            browser.close()
    finally:
        write_report(run / "bestbuy_report.csv", records)
        print("\nReport:", run / "bestbuy_report.csv")
        print("Preview only. No Shopify access or price changes. No Amazon files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
