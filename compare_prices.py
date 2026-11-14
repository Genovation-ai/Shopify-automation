import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from listing_matcher import extract_title_details

folder = Path(__file__).resolve().parent

try:
    path = folder / "amazon_checks.csv"

    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("No Amazon records. Run browser_check.py first.")

    amazon = max(
        rows,
        key=lambda row: datetime.fromisoformat(row["checked_at"]),
    )

    checked_at = datetime.fromisoformat(amazon["checked_at"])
    age = datetime.now().astimezone() - checked_at

    if age > timedelta(hours=24) or age < -timedelta(minutes=5):
        raise ValueError("Amazon record needs a fresh check.")

    details = extract_title_details(amazon["product_title"])

    if (
        amazon["store"] != "Amazon.ca"
        or str(details["model"]).casefold() != "iphone 12"
        or details["storage_gb"] != 64
        or details["color"] != "Black"
        or amazon["condition"] != "Refurbished - Excellent"
        or "unlocked" not in amazon["product_title"].casefold()
        or amazon["product_url"] != "https://www.amazon.ca/dp/B08PP5MSVB"
    ):
        raise ValueError("Amazon offer does not match the Excellent pilot.")

    competitor_price = Decimal(amazon["price_cad"].replace(",", ""))

    if not competitor_price.is_finite() or competitor_price <= 0:
        raise ValueError("Invalid Amazon price.")

    proposed_price = competitor_price - Decimal("3.00")

    if proposed_price <= 0:
        raise ValueError("Calculated price must be positive.")

    print("Product: iPhone 12, 64GB, Black, Excellent")
    print("Eligible competitor: Amazon.ca — Excellent")
    print("Checked at:", amazon["checked_at"])
    print(f"Recorded Amazon price: ${competitor_price:.2f} CAD")
    print(f"Proposed Shopify price: ${proposed_price:.2f} CAD")
    print("Variant ID: 41514825973807")
    print("Source:", amazon["product_url"])
    print("Proposal only. Shopify price has not been changed.")

except (
    ValueError, KeyError, TypeError, OSError, InvalidOperation
) as error:
    raise SystemExit(f"HOLD: {error}")