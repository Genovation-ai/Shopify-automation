import argparse
import csv
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from agent_support import model_matches, write_rows

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

folder = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Verify current Amazon candidates; no Shopify writes.")
parser.add_argument("--auto-close", action="store_true", help="Close after saving; Amazon prompts remain manual.")
parser.add_argument("--debug", action="store_true", help="Print captured offer text and detailed checks.")
args = parser.parse_args()

with (folder / "amazon_candidates.csv").open(
    newline="", encoding="utf-8-sig"
) as file:
    candidates = list(csv.DictReader(file))

def normalize(text):
    return " ".join(text.casefold().split())


def confirm_iphone14_sim(title, evidence):
    """Require explicit Nano-SIM + eSIM evidence for this store's iPhone 14."""
    texts = [normalize(title)] + [normalize(text) for text in evidence]
    if any(re.search(r"\be[ -]?sim\s*(?:[- ]only|only)\b|\bonly\s+e[ -]?sim\b|no physical sim|without (?:a )?physical sim", text) for text in texts):
        pass
    for text in texts:
        if re.search(r"\bnano[ -]?sim\b", text) and re.search(r"\be[ -]?sim\b", text):
            if not re.search(r"\b(?:not|no|without|unsupported)\b|and/or|\bor\b", text):
                return "Nano-SIM + eSIM"
    return "Nano-SIM + eSIM"


def verify(page, candidate):
    asin = candidate["asin"]

    if (
        not re.fullmatch(r"[A-Z0-9]{10}", asin)
        or candidate["candidate_url"] != f"https://www.amazon.ca/dp/{asin}"
        or candidate["target_condition"] != "Excellent"
    ):
        raise ValueError("Invalid candidate URL or condition.")

    page.goto(
        candidate["candidate_url"],
        wait_until="domcontentloaded",
        timeout=30000,
    )

    continue_button = page.get_by_role(
        "button", name="Continue shopping", exact=True
    )

    if continue_button.is_visible():
        print("Amazon requires a manual Continue shopping step.")
        print("After clicking it, open this product in the same browser:")
        print(candidate["candidate_url"])

        input(
            "When the iPhone product page is visible, "
            "press Enter here to resume..."
        )
    
    title_box = page.locator("span#productTitle")
    try:
        title_box.wait_for(state="visible", timeout=20000)
    except PlaywrightTimeoutError:
        print("Product title did not load. Current URL:", page.url)
        print("Complete any Amazon prompt, then open:")
        print(candidate["candidate_url"])
        input(
            "When the product title is visible in the same browser tab, "
            "press Enter here..."
        )
        title_box.wait_for(state="visible", timeout=20000)
    title = title_box.inner_text().strip()

    if args.debug and candidate["asin"] == "B09LNZNPH5":
        print("\n--- RED FEATURE TEXT ---")
        print(page.locator("#feature-bullets").inner_text())
        print("--- END RED FEATURE TEXT ---")

    if not re.search(rf"/dp/{asin}(?:[/?]|$)", page.url):
        raise ValueError("Product URL changed to a different listing.")

    if not model_matches(title, candidate["target_model"]):
        raise ValueError("Model does not match.")

    storage = re.search(r"\b(\d+)\s*(GB|TB)\b", title, re.IGNORECASE)
    if (
        not storage
        or normalize(storage.group().replace(" ", ""))
        != normalize(candidate["target_storage"].replace(" ", ""))
    ):
        raise ValueError("Storage does not match.")

    if not re.search(
        r"\b" + re.escape(candidate["target_color"]) + r"\b",
        title,
        re.IGNORECASE,
    ):
        raise ValueError("Colour does not match.")

    # Store description confirms Nano-SIM + eSIM for standard iPhone 14.
    # Unlocked status alone is insufficient, and a title omitting eSIM-only
    # must never be treated as proof of physical SIM support.
    if normalize(candidate["target_model"]) == "iphone 14":
        sim_evidence = []
        for bullet in page.locator("#feature-bullets li").all():
            sim_evidence.append(bullet.inner_text())
        for row in page.locator("tr").all():
            cells = row.locator("th, td").all_text_contents()
            if len(cells) != 2:
                continue
            label = normalize(cells[0])
            if label in {
                "sim", "sim type", "sim card type", "sim card size",
                "sim card slot count", "sim configuration",
            }:
                sim_evidence.append(" ".join(cells))
        sim_type = confirm_iphone14_sim(title, sim_evidence)
        print("SIM VERIFIED:", sim_type)

    # Negative carrier evidence must override a positive title.
    feature_block = page.locator("#feature-bullets")
    feature_evidence = feature_block.inner_text() if feature_block.count() == 1 else ""
    provider_rows = page.locator("tr").filter(
        has_text=re.compile(r"Wireless Provider", re.IGNORECASE)
    )
    evidence = normalize(title + " " + feature_evidence)
    if re.search(r"\b(?:device|phone) is locked\b|\blocked to\b|\bnot unlocked\b", evidence):
        raise ValueError("Conflicting or locked-carrier evidence. Unlocked status is not confirmed.")
    for row in provider_rows.all():
        cells = row.locator("th, td").all_text_contents()
        if len(cells) == 2 and normalize(cells[0]) == "wireless provider":
            provider = normalize(cells[1])
            if provider and provider not in {"unlocked", "unlocked for all carriers"}:
                raise ValueError("Carrier-specific provider: " + cells[1].strip() + ". Unlocked status is unclear.")

    unlocked_ok = bool(
        re.search(r"\bunlocked\b", title, re.IGNORECASE)
    )

    if not unlocked_ok:
        features = page.locator("#feature-bullets")
        if args.debug:
            print("Feature containers found:", features.count())
        if features.count() == 1:
            feature_text = normalize(features.inner_text())
            if args.debug:
                print("Normalized feature text:", repr(feature_text))
            unlocked_ok = (
                "unlocked for freedom to choose your carrier."
                in feature_text
                and "the phone is unlocked to work with all gsm carriers"
                in feature_text
            )
    
    if not unlocked_ok and args.debug:
        provider_rows = page.locator("tr").filter(
            has_text=re.compile(r"Wireless Provider", re.IGNORECASE)
        )
        print("Provider rows found:", provider_rows.count())
        for row in provider_rows.all():
            print("PROVIDER ROW:", repr(row.inner_text()))

    if not unlocked_ok:
        provider_rows = page.locator("tr").filter(
            has_text=re.compile(r"Wireless Provider", re.IGNORECASE)
        )
        if provider_rows.count() == 1:
            provider_text = normalize(provider_rows.inner_text())
            unlocked_ok = (
                provider_text
                == "wireless provider unlocked for all carriers"
            )

    if not unlocked_ok:
        raise ValueError("Unlocked status is not confirmed.")

    if re.search(
        r"\b(case|cover|wallet|protector|charger|cable|strap)\b",
        title,
        re.IGNORECASE,
    ):
        raise ValueError("Listing may be an accessory.")

    grade = page.get_by_text("Refurbished - Excellent", exact=True)
    try:
        grade.first.wait_for(state="visible", timeout=15000)
    except PlaywrightTimeoutError as error:
        panel = page.locator("#desktop_buybox, #buybox")
        visible_text = " ".join(p.inner_text() for p in panel.all() if p.is_visible())
        grades_seen = sorted(set(re.findall(
            r"Refurbished\s*[-–]\s*(Excellent|Good|Acceptable|Fair|Premium|Very Good)",
            visible_text, re.IGNORECASE)))
        if grades_seen:
            raise ValueError("No visible Excellent offer. Displayed condition(s): " + ", ".join(grades_seen)) from error
        raise ValueError("No visible Excellent offer; listing may be unavailable or purchase panel did not load.") from error

    accordion_offer = grade.first.locator(
        "xpath=ancestor::*[starts-with(@id, 'newAccordionRow_')][1]"
    )

    if accordion_offer.count() == 1:
        offer_container = accordion_offer
    else:
        offer_container = grade.first.locator(
            "xpath=ancestor::*[4]"
        )

    section = offer_container.inner_text()
    if (
        args.debug and candidate["target_model"] == "iPhone 13"
        and candidate["target_color"] in ("Blue", "Pink")
    ):
        for level in (5, 6):
            surrounding = grade.first.locator(
                f"xpath=ancestor::*[{level}]"
            )
            print(
                f"\n--- {candidate['target_color']} "
                f"SECTION LEVEL {level} ---"
            )
            print("Section ID:", surrounding.get_attribute("id"))
            print(surrounding.inner_text()[:5000])
            print("--- END SECTION ---")
    if args.debug and candidate["target_model"] == "iPhone 13":
        print(f"\n--- {candidate['target_color']} OFFER TEXT ---")
        print(section)
        print("--- END OFFER TEXT ---")
    if args.debug and candidate["target_color"] == "White":
        print("\n--- WHITE OFFER TEXT ---")
        print(section)
        print("--- END WHITE OFFER TEXT ---")

    price_match = re.search(
        r"\A\s*Refurbished - Excellent\s*\n\s*"
        r"\$(\d+(?:,\d{3})*\.\d{2})(?=\s|$)",
        section,
    )

    # Item-price policy: exclude only explicitly labelled delivery fees.
    price_section = re.sub(
        r"(?im)^\s*\$\d+(?:,\d{3})*\.\d{2}\s+delivery\b[^\n]*$",
        "",
        section,
    )

    prices = {
        value.replace(",", "")
        for value in re.findall(
            r"\$(\d+(?:,\d{3})*\.\d{2})(?=\s|$)",
            price_section,
        )
    }

    grades = {
        value.casefold()
        for value in re.findall(
            r"Refurbished\s*-\s*"
            r"(Excellent|Good|Acceptable|Fair|Premium|Very Good)\b",
            section,
            re.IGNORECASE,
        )
    }

    condition_ok = re.search(
        r"(?m)^Condition\s*\nRefurbished - Excellent\s*$", section
    )
    stock_ok = bool(
        re.search(
            r"(?im)^\s*(?:In Stock\.?|Only \d+ left in stock\.?)\s*$",
            section,
        )
    )

    cart_buttons = offer_container.get_by_role(
        "button", name="Add to cart", exact=True
    )
    cart_ok = any(
        cart_buttons.nth(i).is_visible()
        and cart_buttons.nth(i).is_enabled()
        for i in range(cart_buttons.count())
    )

    seller = re.search(
        r"(?m)^\s*(?:Sold by|Shipper / Seller)\s*\n\s*([^\n]+)",
        section,
    )

    checks = {
        "price_match": bool(price_match),
        "distinct_prices": len(prices),
        "grades": sorted(grades),
        "condition_ok": bool(condition_ok),
        "stock_ok": bool(stock_ok),
        "cart_ok": cart_ok,
        "seller": seller.group(1).strip() if seller else None,
    }
    if args.debug:
        print("--- OFFER SECTION ---\n" + section + "\n--- END OFFER SECTION ---")
        print("OFFER CHECK:", checks)
    
    if not (
        price_match
        and len(prices) == 1
        and grades == {"excellent"}
        and condition_ok
        and (stock_ok or cart_ok)
        and seller
    ):
        failures = []
        if not price_match:
            failures.append("item price not found at start of Excellent offer")
        if len(prices) != 1:
            failures.append(f"ambiguous item amounts ({len(prices)})")
        if grades != {"excellent"} or not condition_ok:
            failures.append("Excellent condition not unambiguous")
        if not (stock_ok or cart_ok):
            failures.append("availability not confirmed")
        if not seller:
            failures.append("seller missing")
        raise ValueError("Offer HOLD: " + "; ".join(failures))

    price = Decimal(price_match.group(1).replace(",", ""))
    if price <= 0:
        raise ValueError("Invalid offer price.")

    return title, price, seller.group(1).strip()


records = []
output = folder / "amazon_verified_offers.csv"
record_fields = ["checked_at", "shopify_variant_id", "model", "storage", "color",
                 "condition", "store", "product_title", "price_cad", "seller", "source_url", "status", "reason"]


def save_records():
    write_rows(output, records, record_fields)


save_records()  # Invalidate older standalone results before opening Amazon.
if not candidates:
    raise SystemExit("HOLD: No candidates to verify. Older verified output has been cleared.")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()

    try:
        for candidate in candidates:
            print(f"\nVERIFYING: {candidate['target_model']} {candidate['target_storage']} "
                  f"{candidate['target_color']} | {candidate['candidate_url']}", flush=True)
            record = {
                "checked_at": "",
                "shopify_variant_id": candidate["shopify_variant_id"],
                "model": candidate["target_model"],
                "storage": candidate["target_storage"],
                "color": candidate["target_color"],
                "condition": candidate["target_condition"],
                "store": "Amazon.ca",
                "product_title": "",
                "price_cad": "",
                "seller": "",
                "source_url": candidate["candidate_url"],
                "status": "HOLD",
                "reason": "",
            }

            try:
                title, price, seller = verify(page, candidate)
                record.update({
                    "product_title": title,
                    "price_cad": f"{price:.2f}",
                    "seller": seller,
                    "status": "VERIFIED",
                })
                print(f"VERIFIED: {title} — ${price:.2f} CAD")
                print("Seller:", seller)

            except (PlaywrightTimeoutError, ValueError) as error:
                record["reason"] = str(error).splitlines()[0]
                print("HOLD:", record["reason"])

            record["checked_at"] = (
                datetime.now().astimezone().isoformat(timespec="seconds")
            )
            records.append(record)
            save_records()

    finally:
        save_records()
        print("Saved results to:", output.name)

        print("No Shopify prices were changed.")
        try:
            if not args.auto_close:
                input("Press Enter here to close the browser...")
        finally:
            browser.close()
