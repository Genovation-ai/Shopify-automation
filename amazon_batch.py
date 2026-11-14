import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from agent_support import model_matches, search_model_name, write_rows

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

folder = Path(__file__).resolve().parent
FIELDS = ["checked_at", "shopify_variant_id", "target_model", "target_storage",
          "target_color", "target_condition", "search_query", "candidate_title",
          "asin", "candidate_url", "status"]


def queries_for(target):
    base = (f"renewed {search_model_name(target['product_title'])} "
            f"{target['storage']} {target['color']}")
    if target["product_title"] == "iPhone 14":
        # Run both queries: a matching title does not establish SIM support.
        return [base + " nano SIM eSIM Excellent",
                base + " physical SIM unlocked renewed Excellent"]
    return [base + " Excellent"]


def search_ready(page):
    search_box = page.locator(
        'input#twotabsearchtextbox:visible, '
        'input[name="field-keywords"]:visible'
    ).first
    while True:
        try:
            search_box.wait_for(state="visible", timeout=15000)
            if search_box.is_enabled() and search_box.is_editable():
                return search_box
        except PlaywrightTimeoutError:
            pass
        print("Amazon search box is not ready:", page.url)
        answer = input(
            "Complete the Amazon prompt in THIS browser window. "
            "Press Enter to check again, or type q to stop safely: "
        ).strip().casefold()
        if answer == "q":
            raise ValueError("Search stopped by user; do not verify partial results.")


def collect(page, target, search_query, seen, debug=False):
    candidates = []
    cards = page.locator('[data-component-type="s-search-result"][data-asin]')
    for card in cards.all():
        asin = card.get_attribute("data-asin") or ""

        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        if asin in seen:
            continue

        heading_texts = card.locator("h2").all_text_contents()

        titles = list(dict.fromkeys(
            " ".join(text.split())
            for text in heading_texts
            if text.strip()
        ))

        if debug:
            print(f"Result ASIN: {asin} | Headings: {titles}")

        # Remove the category label, keeping the product title.
        titles = [
            text for text in titles
            if text.casefold() != "amazon renewed"
        ]
        if len(titles) != 1:
            print("SKIPPED: Missing or ambiguous product title.")
            continue

        title = titles[0]
        if target["product_title"] == "iPhone 14" and re.search(
            r"\be[\s-]?sim[\s-]*only\b|\bonly[\s-]+e[\s-]?sim\b",
            title, re.IGNORECASE,
        ):
            print("SKIPPED: Explicit eSIM-only title —", title)
            continue
        # Reject accessories and require exact phone details.
        if re.search(
            r"\b(case|cover|wallet|protector|charger|cable|strap)\b",
            title,
            flags=re.IGNORECASE,
        ):
            continue

        if not model_matches(title, target["product_title"]):
            continue

        storage_pattern = (
            r"\b"
            + re.escape(target["storage"]).replace("GB", r"\s*GB").replace("TB", r"\s*TB")
            + r"\b"
        )

        if not re.search(storage_pattern, title, re.IGNORECASE):
            continue

        if not re.search(
            r"\b" + re.escape(target["color"]) + r"\b",
            title,
            re.IGNORECASE,
        ):
            continue

        seen.add(asin)
        candidates.append({
            "checked_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "shopify_variant_id": target["variant_id"],
            "target_model": target["product_title"],
            "target_storage": target["storage"],
            "target_color": target["color"],
            "target_condition": target["condition"],
            "search_query": search_query,
            "candidate_title": title,
            "asin": asin,
            "candidate_url": f"https://www.amazon.ca/dp/{asin}",
            "status": "NEEDS PRODUCT-PAGE VERIFICATION",
        })

        print("Candidate:", title)
        print("URL:", candidates[-1]["candidate_url"])

        if len(candidates) >= 5:
            break

    return candidates


def selection_args(parser):
    parser.add_argument("--model", required=True, help='Exact catalogue model, e.g. "iPhone 11".')
    parser.add_argument("--start", type=int, default=0, help="Number of matching variants to skip (default: 0).")
    parser.add_argument("--limit", type=int, default=3, help="Batch size, 1 to 5 (default: 3).")
    parser.add_argument("--list-targets", action="store_true", help="Print selection without browser or Shopify access.")


def select_targets(folder, args):
    if args.start < 0 or not 1 <= args.limit <= 5:
        raise ValueError("--start must be nonnegative; --limit must be between 1 and 5.")
    with (folder / "competitor_searches.csv").open(newline="", encoding="utf-8-sig") as file:
        matching = [row for row in csv.DictReader(file)
                    if row["competitor_source"] == "Amazon.ca"
                    and row["condition"] == "Excellent"
                    and row["product_title"] == args.model]
    targets = matching[args.start:args.start + args.limit]
    if not targets:
        raise ValueError(f"No targets: {args.model} has {len(matching)} Amazon Excellent entries; start={args.start}.")
    print(f"Selected {len(targets)} of {len(matching)} entries for {args.model}; skipping {args.start}.", flush=True)
    for index, row in enumerate(targets, args.start):
        print(f"[{index}] {row['product_title']} {row['storage']} {row['color']} | {row['variant_id']}", flush=True)
    return targets


def main():
    parser = argparse.ArgumentParser(description="Search a selected Amazon Excellent batch. No Shopify writes.")
    selection_args(parser)
    parser.add_argument("--auto-close", action="store_true", help="Close browser after saving; manual Amazon prompts remain.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    try:
        targets = select_targets(folder, args)
    except ValueError as error:
        parser.error(str(error))
    if args.list_targets:
        return
    results = []
    failed = False
    # Invalidate old standalone output before any browser action.
    output = folder / "amazon_candidates.csv"
    def save():
        write_rows(output, results, FIELDS)
    save()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto("https://www.amazon.ca/", wait_until="domcontentloaded", timeout=30000)
            for number, target in enumerate(targets, 1):
                print(f"\nSearch {number}/{len(targets)}: {target['product_title']} {target['storage']} {target['color']}")
                seen = set()
                for search_query in queries_for(target):
                    print("Actual search query:", search_query)
                    search_box = search_ready(page)
                    search_box.fill(search_query)
                    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                        search_box.press("Enter")
                    print("Results URL:", page.url)
                    cards = page.locator('[data-component-type="s-search-result"][data-asin]')
                    try:
                        cards.first.wait_for(state="visible", timeout=20000)
                    except PlaywrightTimeoutError:
                        body = page.locator("body").inner_text().casefold()
                        if "no results for" in body or "did not match any products" in body:
                            print("NO MATCH: Amazon returned no results for this query.")
                            continue
                        raise
                    results.extend(collect(page, target, search_query, seen, args.debug))
                if not seen:
                    print("NO MATCH: No eligible title candidates for this variant.")
        except (PlaywrightTimeoutError, ValueError) as error:
            failed = True
            print("HOLD:", str(error).splitlines()[0])
            print("Page URL:", page.url)
            print("Stopping this batch. No prices collected.")
        finally:
            save()
            print(f"\nSaved {len(results)} candidates to {output.name}")
            print("No prices collected. No Shopify prices changed.")
            try:
                if not args.auto_close:
                    input("Press Enter here to close the browser...")
            finally:
                browser.close()
    if failed:
        raise SystemExit("HOLD: Search batch interrupted; do not verify partial results. Rerun the search.")
    if not results:
        print("NO MATCH: No eligible candidates. Current output is empty; do not reuse earlier offers.")


if __name__ == "__main__":
    main()
