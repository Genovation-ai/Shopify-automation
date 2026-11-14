import csv
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()

    try:
        page.goto("https://www.amazon.ca/", wait_until="domcontentloaded", timeout=30000)
        print("Page URL:", page.url)
        print("Page title:", page.title())

        search_box = page.locator("#twotabsearchtextbox")
        search_box.wait_for(state="visible", timeout=10000)
        search_box.fill("renewed iPhone 12 64GB Black Excellent")
        search_box.press("Enter")
        print("Search submitted.")
        page.wait_for_url("**/s?*", timeout=15000)

        phone_links = page.locator('a[href*="/dp/B08PP5MSVB"]')
        print("Links to our known iPhone 12 listing:", phone_links.count())
        print("Results page:", page.url)
        phone_links.first.click()
        page.locator("span#productTitle").wait_for(state="visible", timeout=15000)

        print("Product URL:", page.url)
        print("Product title:", page.locator("span#productTitle").inner_text().strip())
        prices = page.locator("#corePriceDisplay_desktop_feature_div .a-offscreen").all_text_contents()
        print("Price text found:", prices)

        page_text = page.locator("body").inner_text()
        for word in ("Excellent", "Good", "Acceptable"):
            position = page_text.find(word)
            if position != -1:
               print(f"Text near {word}:", page_text[max(0, position - 60):position + 100])

    except PlaywrightTimeoutError:
        print("The Amazon search box did not appear.")
        page.screenshot(path="amazon_page.png")
        print("Saved a screenshot: amazon_page.png")

        browser.close()
        raise SystemExit("HOLD: Amazon search did not load; no price was recorded.")

    except Exception as error:
        print("Browser error:", error)
        browser.close()
        raise SystemExit("HOLD: Browser step failed; no price was recorded.")

    title = page.locator("span#productTitle").inner_text().strip()

    expected_details = {
        "model": "iPhone 12",
        "storage_gb": 64,
        "color": "Black",
    }

    from listing_matcher import extract_title_details

    extracted = extract_title_details(title)

    for field, expected in expected_details.items():
        actual = extracted.get(field)

        if str(actual).casefold() != str(expected).casefold():
            browser.close()
            raise SystemExit(
                f"HOLD: Product {field} does not match. No price recorded."
            )

    print("Product check passed: iPhone 12, 64GB, Black.")
    grade = page.get_by_text("Refurbished - Excellent", exact=True)

    if grade.count() > 0:
        section = grade.first.locator("xpath=ancestor::*[4]").inner_text()

        price_match = re.search(
            r"\A\s*Refurbished - Excellent\s*\n\s*"
            r"\$(\d+(?:,\d{3})*\.\d{2})(?=\s|$)",
            section,
        )

        prices_found = {
            value.replace(",", "")
            for value in re.findall(
                r"\$(\d+(?:,\d{3})*\.\d{2})(?=\s|$)",
                section,
            )
        }

        condition_confirmed = re.search(
            r"(?m)^Condition\s*\nRefurbished - Excellent\s*$",
            section,
        )

        grades_found = set(
            re.findall(
                r"Refurbished\s*-\s*"
                r"(Excellent|Good|Acceptable|Fair|Premium|Very Good)\b",
                section,
                flags=re.IGNORECASE,
            )
        )

        stock_confirmed = re.search(
            r"(?im)^(?:In Stock|Only \d+ left in stock\.?)\s*$",
            section,
        )

        offer_confirmed = (
            price_match is not None
            and len(prices_found) == 1
            and condition_confirmed is not None
            and {value.lower() for value in grades_found} == {"excellent"}
            and stock_confirmed is not None
            and "Add to cart" in section
            and "Sold by" in section
        )

        if not offer_confirmed:
            browser.close()
            raise SystemExit(
                "HOLD: Offer price, condition, or availability is unclear. "
                "No price or proposal saved."
            )

        print("Offer checks passed: Excellent, one price, and in stock.")

        if price_match:
            file_path = Path("amazon_checks.csv")
            new_file = not file_path.exists()

            with file_path.open("a", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)

                if new_file:
                  writer.writerow([
                      "checked_at", "store", "product_title",
                      "condition", "price_cad", "product_url"
                  ])

                writer.writerow([
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    "Amazon.ca",
                    page.locator("span#productTitle").inner_text().strip(),
                    "Refurbished - Excellent",
                    price_match.group(1),
                    "https://www.amazon.ca/dp/B08PP5MSVB",
            ])

            print(f"Saved ${price_match.group(1)} to {file_path}")
            from decimal import Decimal

            competitor_price = Decimal(price_match.group(1).replace(",", ""))
            candidate_price = competitor_price - Decimal("3.00")

            print(f"Candidate Shopify price: ${candidate_price:.2f} CAD")
            print("Proposal only. Shopify price has not been changed.")
            proposal_path = Path("price_proposals.csv")
            needs_header = (
                not proposal_path.exists()
                or proposal_path.stat().st_size == 0
            )

            with proposal_path.open(
                "a", newline="", encoding="utf-8-sig"
            ) as file:
                writer = csv.writer(file)

                if needs_header:
                    writer.writerow([
                        "checked_at",
                        "product_title",
                        "condition",
                        "competitor",
                        "competitor_price_cad",
                        "discount_cad",
                        "proposed_shopify_price_cad",
                        "shopify_variant_id",
                        "source_url",
                        "status",
                    ])

                writer.writerow([
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    title,
                    "Refurbished - Excellent",
                    "Amazon.ca",
                    f"{competitor_price:.2f}",
                    "3.00",
                    f"{candidate_price:.2f}",
                    "41514825973807",
                    "https://www.amazon.ca/dp/B08PP5MSVB",
                    "PROPOSAL ONLY - not applied",
                ])

            print("Proposal saved to:", proposal_path)
            print("Opening Shopify sign-in for the account owner...")
            page.goto("https://www.shopify.com/login", wait_until="domcontentloaded")
            print("STOP: Shopify sign-in and two-step verification must be completed by an authorized person.")
        else:
             print("HOLD: Could not find a price beside the Excellent condition.")
    else:
        print("HOLD: Could not confirm the Excellent condition.")
    
    input("Press Enter here to close the browser...")
    browser.close()