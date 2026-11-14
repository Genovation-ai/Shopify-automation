import csv
from pathlib import Path

import catalog_check as checked

folder = Path(__file__).resolve().parent

# catalog_check records the CSV line number of each rejected row.
rejected_lines = {
    line_number
    for line_number, variant_id, reason in checked.issues
}

prepared = []

for line_number, row in enumerate(checked.rows, start=2):
    if line_number in rejected_lines:
        continue

    source = row["competitor_source"]

    search_query = (
        f"refurbished {row['product_title']} "
        f"{row['storage']} {row['color']} "
        f"{row['condition']} unlocked"
    )

    prepared.append({
        **row,
        "search_query": search_query,
        "search_status": "NOT CHECKED",
    })

if not prepared:
    raise SystemExit("HOLD: No catalogue entries passed the checks.")

file_path = folder / "competitor_searches.csv"
temporary_path = file_path.with_suffix(".tmp")

with temporary_path.open("w", newline="", encoding="utf-8-sig") as file:
    writer = csv.DictWriter(file, fieldnames=list(prepared[0]))
    writer.writeheader()
    writer.writerows(prepared)

temporary_path.replace(file_path)

amazon_rows = [
    row for row in prepared
    if row["competitor_source"] == "Amazon.ca"
]

print(f"\nSaved {len(prepared)} entries to {file_path.name}")
print(f"Amazon Excellent searches: {len(amazon_rows)}")
print(f"Best Buy searches: {len(prepared) - len(amazon_rows)}")

print("\nFirst five Amazon search queries:")
for row in amazon_rows[:5]:
    print("-", row["search_query"])

print("\nSearch preparation only. No competitor prices collected.")
print("No Shopify prices changed.")