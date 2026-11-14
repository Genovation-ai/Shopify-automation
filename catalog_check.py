import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

path = Path(__file__).with_name("iphone_catalog.csv")

try:
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
except OSError as error:
    raise SystemExit(f"HOLD: Could not read catalogue: {error}")

if not rows:
    raise SystemExit("HOLD: Catalogue is empty.")

conditions = Counter()
sources = Counter()
seen_ids = set()
issues = []
ready = 0

for line_number, row in enumerate(rows, start=2):
    problems = []

    conditions[row.get("condition", "")] += 1
    sources[row.get("competitor_source", "")] += 1

    required = (
        "product_title", "product_type", "storage", "color", "condition",
        "current_price", "currency", "product_id", "variant_id",
    )

    for field in required:
        if not row.get(field, "").strip():
            problems.append(f"missing {field}")

    variant_id = row.get("variant_id", "")

    if variant_id in seen_ids:
        problems.append("duplicate variant ID")
    seen_ids.add(variant_id)

    for field, object_type in (
        ("variant_id", "ProductVariant"),
        ("product_id", "Product"),
    ):
        pattern = rf"gid://shopify/{object_type}/\d+"
        if not re.fullmatch(pattern, row.get(field, "")):
            problems.append(f"invalid {field}")

    if row.get("product_status") != "ACTIVE":
        problems.append("product is not active")

    if row.get("product_type", "").strip().casefold() != "phone":
        problems.append("product type needs review")

    if row.get("currency") != "CAD":
        problems.append("currency is not CAD")

    if not re.fullmatch(
        r"\d+(?:GB|TB)", row.get("storage", ""), flags=re.IGNORECASE
    ):
        problems.append("storage needs review")

    try:
        price = Decimal(row["current_price"])
        if not price.is_finite() or price <= 0:
            problems.append("price must be positive")
    except (InvalidOperation, KeyError):
        problems.append("invalid price")

    expected_source = {
        "excellent": "Amazon.ca",
        "very good": "DERIVED_MIDPOINT",
        "good": "BestBuy.ca",
        "fair": "BestBuy.ca",
    }.get(row.get("condition", "").strip().casefold())

    if expected_source is None:
        problems.append("condition has no pricing rule")
    elif row.get("competitor_source") != expected_source:
        problems.append("competitor source does not match condition")

    try:
        options = {
            item["name"].casefold(): item["value"]
            for item in json.loads(row["options_json"])
        }
        if (
            options.get("size", options.get("storage")) != row["storage"]
            or options.get("color", options.get("colour")) != row["color"]
            or options.get("condition") != row["condition"]
        ):
            problems.append("exported details differ from Shopify options")
    except (ValueError, KeyError, TypeError):
        problems.append("invalid options data")

    if problems:
        issues.append((line_number, variant_id, "; ".join(problems)))
    else:
        ready += 1

print(f"Total entries: {len(rows)}")
print(f"Passed catalogue checks: {ready}")
print(f"Entries needing review: {len(issues)}")

print("\nConditions:")
for condition, count in sorted(conditions.items()):
    print(f"  {condition or '(missing)'}: {count}")

print("\nCompetitor assignments:")
for source, count in sorted(sources.items()):
    print(f"  {source or '(missing)'}: {count}")

if issues:
    print("\nFirst 10 entries needing review:")
    for line_number, variant_id, reason in issues[:10]:
        print(f"  CSV line {line_number}: {variant_id} — {reason}")

print("\nCatalogue check only. No files or Shopify prices were changed.")
print("Passing these checks does not confirm a matching competitor offer.")