import argparse
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from listing_matcher import extract_title_details

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

try:
    # Read the latest Amazon check.
    path = Path(__file__).with_name("amazon_checks.csv")
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("No Amazon records.")

    offer = max(
        rows,
        key=lambda row: datetime.fromisoformat(row["checked_at"]),
    )

    age = (
        datetime.now().astimezone()
        - datetime.fromisoformat(offer["checked_at"])
    )

    if age > timedelta(minutes=15) or age < -timedelta(minutes=5):
        raise ValueError("Run browser_check.py again. Offer must be fresh.")

    details = extract_title_details(offer["product_title"])

    if (
        offer["store"] != "Amazon.ca"
        or str(details["model"]).casefold() != "iphone 12"
        or details["storage_gb"] != 64
        or details["color"] != "Black"
        or offer["condition"] != "Refurbished - Excellent"
        or "unlocked" not in offer["product_title"].casefold()
        or offer["product_url"] != "https://www.amazon.ca/dp/B08PP5MSVB"
    ):
        raise ValueError("Amazon offer does not match the pilot.")

    amazon_price = Decimal(offer["price_cad"].replace(",", ""))
    target = amazon_price - Decimal("3.00")

    if not target.is_finite() or target <= 0:
        raise ValueError("Invalid proposed price.")

    # Reuse our existing Shopify connection and read-only check.
    import shopify_check as shopify

    if shopify.domain != "shop-techgenius.myshopify.com":
        raise ValueError("Unexpected Shopify store.")

    variant = shopify.variant
    options = {
        item["name"]: item["value"]
        for item in variant["selectedOptions"]
    }

    if (
        variant["id"] != "gid://shopify/ProductVariant/41514825973807"
        or variant["product"]["title"] != "iPhone 12"
        or options.get("Size") != "64GB"
        or options.get("Color") != "Black"
        or options.get("Condition") != "Excellent"
        or shopify.data["shop"]["currencyCode"] != "CAD"
    ):
        raise ValueError("Shopify variant or currency does not match.")

    current = Decimal(variant["price"])

    print(f"\nAmazon price: ${amazon_price:.2f} CAD")
    print(f"Shopify change: ${current:.2f} → ${target:.2f} CAD")

    if current == target:
        raise SystemExit("Already at the proposed price. No update needed.")

    # Limit this first pilot to the exact change we reviewed.
    if current != Decimal("307.00") or target != Decimal("306.99"):
        raise ValueError("Prices changed. Review the new proposal first.")

    if not args.apply:
        raise SystemExit("PREVIEW ONLY: Shopify price has not been changed.")

    mutation = """
    mutation UpdatePrice(
      $productId: ID!,
      $variants: [ProductVariantsBulkInput!]!
    ) {
      productVariantsBulkUpdate(
        productId: $productId,
        variants: $variants
      ) {
        productVariants { id price }
        userErrors { field message }
      }
    }
    """

    response = requests.post(
        f"https://{shopify.domain}/admin/api/2026-10/graphql.json",
        headers={"X-Shopify-Access-Token": shopify.token},
        json={
            "query": mutation,
            "variables": {
                "productId": "gid://shopify/Product/7216252485679",
                "variants": [{
                    "id": variant["id"],
                    "price": f"{target:.2f}",
                }],
            },
        },
        timeout=30,
        allow_redirects=False,
    )

    if response.status_code != 200:
        raise ValueError(
            f"Update response HTTP {response.status_code}. "
            "Check Shopify before retrying."
        )

    result = response.json()
    if result.get("errors"):
        raise ValueError(str(result["errors"]))

    update = result["data"]["productVariantsBulkUpdate"]
    if update["userErrors"]:
        raise ValueError(str(update["userErrors"]))

    updated = update["productVariants"]
    if (
        len(updated) != 1
        or updated[0]["id"] != variant["id"]
        or Decimal(updated[0]["price"]) != target
    ):
        raise ValueError("Unexpected update result. Check Shopify.")

    print(f"Shopify confirmed the updated price: ${target:.2f} CAD")
    print("Run python shopify_check.py to read it back.")

except requests.RequestException:
    raise SystemExit(
        "Connection failed. If applying, check Shopify before retrying."
    )
except (
    ValueError, KeyError, TypeError, OSError, InvalidOperation
) as error:
    raise SystemExit(f"HOLD: {error}")