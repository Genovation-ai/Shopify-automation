import re

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()

    try:
        page.goto(
            "https://www.bestbuy.ca/en-ca",
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Try the search box identified by its placeholder.
        search_box = page.get_by_placeholder(
            re.compile("search", re.IGNORECASE)
        ).first

        search_box.wait_for(state="visible", timeout=20000)
        search_box.fill("refurbished iPhone 12 64GB Black Excellent")
        search_box.press("Enter")

        # Wait for a visible product link in the results.
        product_links = page.locator(
            'a[href*="/en-ca/product/"]:visible'
        )
        product_links.first.wait_for(state="visible", timeout=30000)

        print("Best Buy search completed.")
        print("Results URL:", page.url)

        seen = set()

        for link in product_links.all():
            url = link.get_attribute("href")
            text = link.inner_text().strip()

            if url and url not in seen:
                seen.add(url)
                print("\nListing:", text[:300] or "(Link has no text)")
                print("URL:", url)

            if len(seen) >= 10:
                break

        pilot_link = page.locator(
            'a[href$="/15336571"]:visible'
        ).first

        product_href = pilot_link.get_attribute("href", timeout=10000)

        if not product_href:
            raise RuntimeError("The product link has no URL.")

        from urllib.parse import urljoin

        page.goto(
            urljoin("https://www.bestbuy.ca", product_href),
            wait_until="domcontentloaded",
            timeout=30000,
        )

        heading = page.get_by_role("heading", level=1).first
        heading.wait_for(state="visible", timeout=20000)

        print("\n--- BEST BUY PRODUCT ---")
        print("Product URL:", page.url)
        print("Product title:", heading.inner_text().strip())
        if "access denied" in heading.inner_text().casefold():
            print("HOLD: Best Buy blocked the product page.")
            print("No Best Buy price collected.")
            raise SystemExit()

        print("\n--- PAGE DETAILS ---")
        print(page.locator("body").inner_text()[:12000])
        print("--- END PAGE DETAILS ---")
    except PlaywrightTimeoutError:
        print("HOLD: Search box or product results did not load.")
        print("Page URL:", page.url)
        print("Tell me what the browser displays.")

    except Exception as error:
        print("Browser error:", error)

    finally:
        input("Press Enter here to close the browser...")
        browser.close()