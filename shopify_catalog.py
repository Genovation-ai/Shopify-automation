import csv
import json
import re
from datetime import datetime
from pathlib import Path

import requests
import shopify_check as shopify

query = """
query Catalogue($cursor: String) {
  productVariants(first: 100, after: $cursor) {
    nodes {
      id
      title
      sku
      price
      selectedOptions {
        name
        value
      }
      product {
        id
        title
        status
        productType
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

rows = []
cursor = None
page_number = 0
exported_at = datetime.now().astimezone().isoformat(timespec="seconds")

try:
    while True:
        response = requests.post(
            f"https://{shopify.domain}/admin/api/2026-10/graphql.json",
            headers={"X-Shopify-Access-Token": shopify.token},
            json={"query": query, "variables": {"cursor": cursor}},
            timeout=30,
            allow_redirects=False,
        )

        if response.status_code != 200:
            raise ValueError(f"Shopify returned HTTP {response.status_code}")

        result = response.json()

        if result.get("errors"):
            raise ValueError(str(result["errors"]))

        connection = result["data"]["productVariants"]
        page_number += 1

        for variant in connection["nodes"]:
            product = variant["product"]

            if not re.search(r"\biphone\b", product["title"], re.IGNORECASE):
                continue

            options = {
                item["name"].casefold(): item["value"]
                for item in variant["selectedOptions"]
            }

            condition = options.get("condition", "").strip()
            storage = options.get("size", options.get("storage", "")).strip()
            color = options.get("color", options.get("colour", "")).strip()

            cond_norm = condition.casefold()
            if cond_norm == "excellent":
                source = "Amazon.ca"
            elif cond_norm in ("good", "fair"):
                source = "BestBuy.ca"
            elif cond_norm == "very good":
                source = "DERIVED_MIDPOINT"
            else:
                source = "REVIEW"

            # Missing details or inactive products require review.
            if not storage or not color or product["status"] != "ACTIVE":
                source = "REVIEW"

            rows.append({
                "exported_at": exported_at,
                "product_title": product["title"],
                "product_type": product["productType"],
                "product_status": product["status"],
                "variant_title": variant["title"],
                "storage": storage,
                "color": color,
                "condition": condition,
                "current_price": variant["price"],
                "currency": shopify.data["shop"]["currencyCode"],
                "product_id": product["id"],
                "variant_id": variant["id"],
                "sku": variant["sku"] or "",
                "competitor_source": source,
                "options_json": json.dumps(
                    variant["selectedOptions"], ensure_ascii=False
                ),
            })

        print(f"Page {page_number}: {len(rows)} iPhone entries collected.")

        page_info = connection["pageInfo"]

        if not page_info["hasNextPage"]:
            break

        next_cursor = page_info["endCursor"]
        if not next_cursor or next_cursor == cursor:
            raise ValueError("Pagination did not advance.")

        cursor = next_cursor

    if not rows:
        raise ValueError("No products with iPhone in their title were found.")

    file_path = Path(__file__).with_name("iphone_catalog.csv")
    temporary_path = file_path.with_suffix(".tmp")

    with temporary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    temporary_path.replace(file_path)

    print(f"\nSaved {len(rows)} iPhone entries to {file_path.name}")
    print("Review the catalogue before using it for competitor searches.")
    print("Read-only export complete. No prices were changed.")

except requests.RequestException:
    raise SystemExit("HOLD: Connection failed. Catalogue was not replaced.")
except (ValueError, KeyError, TypeError, OSError) as error:
    raise SystemExit(f"HOLD: {error}")