"""Automated, non-interactive Amazon.ca search & offer verifier (Excellent & Very Good)."""
import argparse
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from agent_support import model_matches, read_rows, search_model_name, write_rows
from fetcher import fetch_html, invalidate_mapping, load_url_cache, record_verified_mapping

folder = Path(__file__).resolve().parent

OFFER_FIELDS = [
    "checked_at", "shopify_variant_id", "model", "storage", "color",
    "condition", "store", "product_title", "price_cad", "seller",
    "source_url", "status", "reason",
]

GRADE_LABELS = {
    "Excellent": "Refurbished - Excellent",
    "Very Good": "Refurbished - Very Good",
}


def normalize(text):
    return " ".join(text.casefold().split())


def queries_for(target):
    base = (
        f"renewed {search_model_name(target['product_title'])} "
        f"{target['storage']} {target['color']}"
    )
    grade_word = target["condition"]
    if target["product_title"] == "iPhone 14":
        return [
            f"{base} nano SIM eSIM {grade_word}",
            f"{base} physical SIM unlocked renewed {grade_word}",
        ]
    return [f"{base} {grade_word}"]


def search_candidates(target):
    seen = set()
    urls = []
    for q in queries_for(target):
        search_url = f"https://www.amazon.ca/s?k={quote_plus(q)}"
        html = fetch_html(search_url)
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select('[data-component-type="s-search-result"][data-asin]'):
            asin = (card.get("data-asin") or "").strip()
            if not re.fullmatch(r"[A-Z0-9]{10}", asin) or asin in seen:
                continue
            headings = [
                " ".join(h.get_text(" ", strip=True).split())
                for h in card.select("h2")
                if h.get_text(strip=True)
            ]
            titles = [t for t in dict.fromkeys(headings) if t.casefold() != "amazon renewed"]
            if len(titles) != 1:
                continue
            title = titles[0]
            if re.search(r"\b(case|cover|wallet|protector|charger|cable|strap)\b", title, re.I):
                continue
            if target["product_title"] == "iPhone 14" and re.search(
                r"\be[\s-]?sim[\s-]*only\b|\bonly[\s-]+e[\s-]?sim\b", title, re.I
            ):
                continue
            if not model_matches(title, target["product_title"]):
                continue
            storage_pat = (
                r"\b"
                + re.escape(target["storage"]).replace("GB", r"\s*GB").replace("TB", r"\s*TB")
                + r"\b"
            )
            if not re.search(storage_pat, title, re.I):
                continue
            if not re.search(r"\b" + re.escape(target["color"]) + r"\b", title, re.I):
                continue
            seen.add(asin)
            urls.append(f"https://www.amazon.ca/dp/{asin}")
            if len(urls) >= 3:
                return urls
    return urls


def confirm_iphone14_sim(title, evidence):
    texts = [normalize(title)] + [normalize(t) for t in evidence]
    if any(
        re.search(
            r"\be[ -]?sim\s*(?:[- ]only|only)\b|\bonly\s+e[ -]?sim\b|"
            r"no physical sim|without (?:a )?physical sim",
            t,
        )
        for t in texts
    ):
        raise ValueError("SIM mismatch: eSIM-only listing; Nano-SIM + eSIM required.")
    for t in texts:
        if re.search(r"\bnano[ -]?sim\b", t) and re.search(r"\be[ -]?sim\b", t):
            if not re.search(r"\b(?:not|no|without|unsupported)\b|and/or|\bor\b", t):
                return
    raise ValueError("SIM compatibility unclear: explicit Nano-SIM + eSIM required.")


def parse_and_verify_product(html, url, target):
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("span#productTitle")
    if not title_el:
        raise ValueError("Amazon product title missing (blocked or unavailable).")
    title = " ".join(title_el.get_text(" ", strip=True).split())

    if not model_matches(title, target["product_title"]):
        raise ValueError("Model does not match.")

    storage = re.search(r"\b(\d+)\s*(GB|TB)\b", title, re.I)
    if not storage or normalize(storage.group().replace(" ", "")) != normalize(
        target["storage"].replace(" ", "")
    ):
        raise ValueError("Storage does not match.")

    if not re.search(r"\b" + re.escape(target["color"]) + r"\b", title, re.I):
        raise ValueError("Colour does not match.")

    bullets = [li.get_text(" ", strip=True) for li in soup.select("#feature-bullets li")]
    table_rows = []
    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.select("th, td")]
        if len(cells) == 2:
            table_rows.append((normalize(cells[0]), cells[1]))

    if normalize(target["product_title"]) == "iphone 14":
        sim_ev = list(bullets)
        for label, val in table_rows:
            if label in {
                "sim", "sim type", "sim card type", "sim card size",
                "sim card slot count", "sim configuration",
            }:
                sim_ev.append(f"{label} {val}")
        confirm_iphone14_sim(title, sim_ev)

    evidence = normalize(title + " " + " ".join(bullets))
    if re.search(r"\b(?:device|phone) is locked\b|\blocked to\b|\bnot unlocked\b", evidence):
        raise ValueError("Conflicting or locked-carrier evidence.")

    provider_ok = False
    for label, val in table_rows:
        if label == "wireless provider":
            prov = normalize(val)
            if prov and prov not in {"unlocked", "unlocked for all carriers"}:
                raise ValueError(f"Carrier-specific provider: {val.strip()}.")
            if prov == "unlocked for all carriers":
                provider_ok = True

    unlocked_ok = bool(re.search(r"\bunlocked\b", title, re.I)) or provider_ok
    if not unlocked_ok:
        feat_text = normalize(" ".join(bullets))
        unlocked_ok = (
            "unlocked for freedom to choose your carrier." in feat_text
            and "the phone is unlocked to work with all gsm carriers" in feat_text
        )
    if not unlocked_ok:
        raise ValueError("Unlocked status is not confirmed.")

    expected_grade = GRADE_LABELS.get(target["condition"])
    if not expected_grade:
        raise ValueError(f"Unsupported Amazon condition: {target['condition']}")

    buybox = soup.select_one("#desktop_buybox") or soup.select_one("#buybox") or soup
    box_text = buybox.get_text("\n", strip=True)

    # Locate the offer block matching the exact target condition
    offer_block = None
    for acc in buybox.select('[id^="newAccordionRow_"]'):
        txt = acc.get_text("\n", strip=True)
        if expected_grade in txt:
            offer_block = txt
            break
    if offer_block is None:
        if expected_grade in box_text:
            offer_block = box_text
        else:
            seen_grades = sorted(
                set(
                    re.findall(
                        r"Refurbished\s*[-–]\s*(Excellent|Very Good|Good|Acceptable|Fair|Premium)",
                        box_text,
                        re.I,
                    )
                )
            )
            if seen_grades:
                raise ValueError(
                    f"No {expected_grade} offer. Displayed: {', '.join(seen_grades)}"
                )
            raise ValueError(f"No {expected_grade} offer visible in buybox.")

    cleaned = re.sub(r"(?im)^\s*\$\d+(?:,\d{3})*\.\d{2}\s+delivery\b[^\n]*$", "", offer_block)
    prices = [
        Decimal(v.replace(",", ""))
        for v in re.findall(r"\$(\d+(?:,\d{3})*\.\d{2})(?=\s|$)", cleaned)
    ]
    unique_prices = sorted(set(prices))
    if not unique_prices:
        raise ValueError("No item price found in buybox.")
    if len(unique_prices) > 1:
        raise ValueError(f"Ambiguous item prices ({len(unique_prices)} found).")

    price = unique_prices[0]
    if price <= Decimal("3.00"):
        raise ValueError("Invalid offer price.")

    if re.search(r"\b(?:currently unavailable|out of stock|temporarily out of stock)\b", offer_block, re.I):
        raise ValueError("Offer is out of stock.")
    if not re.search(r"\b(?:in stock|only \d+ left in stock|add to cart)\b", offer_block, re.I):
        raise ValueError("Stock availability not confirmed.")

    seller_match = re.search(r"(?im)^\s*(?:Sold by|Shipper / Seller)\s*[:\n]\s*([^\n]+)", offer_block)
    seller = seller_match.group(1).strip() if seller_match else "Amazon Renewed Seller"

    return title, price, seller


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
            "store": "Amazon.ca",
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
            if cached and cached.get("store") == "Amazon.ca" and cached.get("url"):
                candidate_urls.append(cached["url"])

            # Try cached URL first; if missing or fails, search Amazon.ca
            verified = False
            last_err = "No matching candidate found on Amazon.ca."
            for attempt_round in range(2):
                if attempt_round == 1:
                    if cached:
                        invalidate_mapping(vid)
                    candidate_urls = [
                        u for u in search_candidates(target) if u not in candidate_urls
                    ]
                for url in candidate_urls:
                    try:
                        html = fetch_html(url)
                        title, price, seller = parse_and_verify_product(html, url, target)
                        rec.update({
                            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "product_title": title,
                            "price_cad": f"{price:.2f}",
                            "seller": seller,
                            "source_url": url,
                            "status": "VERIFIED",
                            "reason": "",
                        })
                        record_verified_mapping(vid, "Amazon.ca", url, title)
                        print(f"[AMAZON VERIFIED] {target['product_title']} {target['storage']} "
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
                print(f"[AMAZON HOLD] {target['product_title']} {target['storage']} "
                      f"{target['color']} ({target['condition']}): {last_err}", flush=True)
        except Exception as exc:
            rec["reason"] = str(exc).splitlines()[0]
            print(f"[AMAZON ERROR] {vid}: {rec['reason']}", flush=True)

        records.append(rec)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-file", type=Path, default=folder / "batch_targets.csv")
    parser.add_argument("--output", type=Path, default=folder / "amazon_verified_offers.csv")
    args = parser.parse_args()

    targets = [
        r for r in read_rows(args.targets_file)
        if r.get("competitor_source") == "Amazon.ca"
    ]
    write_rows(args.output, [], OFFER_FIELDS)
    if not targets:
        print("No Amazon.ca targets in batch_targets.csv.")
        return
    records = process_targets(targets)
    write_rows(args.output, records, OFFER_FIELDS)
    print(f"Saved {len(records)} Amazon.ca records to {args.output.name}")


if __name__ == "__main__":
    main()