import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

# This records an offer you checked manually in your browser.
try:
    price = Decimal(input("Best Buy price you just checked (CAD): $").strip())
    if not price.is_finite() or price <= 0:
        raise ValueError()
except (InvalidOperation, ValueError):
    raise SystemExit("HOLD: Enter a valid positive price.")

confirmed = input(
    "Confirm iPhone 12, 64GB, Black, Unlocked, Excellent, "
    "free shipping, and available to order (yes/no): "
).strip().lower()

if confirmed != "yes":
    raise SystemExit("HOLD: Offer details have not been confirmed.")

seller = input("Seller shown on the offer: ").strip()
if not seller:
    raise SystemExit("HOLD: Seller is missing.")

file_path = Path(__file__).with_name("bestbuy_checks.csv")
needs_header = not file_path.exists() or file_path.stat().st_size == 0

with file_path.open("a", newline="", encoding="utf-8-sig") as file:
    writer = csv.writer(file)

    if needs_header:
        writer.writerow([
            "checked_at", "store", "model", "storage_gb", "color",
            "condition", "unlocked", "price_cad", "shipping_cad",
            "available", "seller", "product_url", "verification_method",
        ])

    writer.writerow([
        datetime.now().astimezone().isoformat(timespec="seconds"),
        "BestBuy.ca",
        "iPhone 12",
        64,
        "Black",
        "Excellent",
        "yes",
        f"{price:.2f}",
        "0.00",
        "yes",
        seller,
        "https://www.bestbuy.ca/en-ca/product/"
        "refurbished-excellent-apple-iphone-12-64gb-black-unlocked/15336571",
        "manual",
    ])

print(f"Manually verified offer saved to {file_path.name}.")
print("Shopify price has not been changed.")