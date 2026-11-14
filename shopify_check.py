import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load the .env file beside this script.
load_dotenv(Path(__file__).with_name(".env"))

domain = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip()
client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()

if not domain:
    raise SystemExit(
        "WAIT: Add the Shopify domain to .env when your boss sends it."
    )

if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
    raise SystemExit(
        "Check the domain: use your-store.myshopify.com without https://"
    )

if not client_id or not client_secret:
    raise SystemExit("HOLD: Client ID or Client secret is missing from .env.")

query = """
query {
  shop {
    name
    currencyCode
  }
  productVariant(id: "gid://shopify/ProductVariant/41514825973807") {
    id
    title
    price
    product {
      title
    }
    selectedOptions {
      name
      value
    }
  }
}
"""

try:
    token_response = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
        allow_redirects=False,
    )

    if token_response.status_code != 200:
        raise SystemExit(
            f"HOLD: Shopify login failed (HTTP {token_response.status_code})."
        )

    token = token_response.json().get("access_token")
    if not token:
        raise SystemExit("HOLD: Shopify did not return an access token.")

    response = requests.post(
        f"https://{domain}/admin/api/2026-10/graphql.json",
        headers={"X-Shopify-Access-Token": token},
        json={"query": query},
        timeout=30,
        allow_redirects=False,
    )

    if response.status_code != 200:
        raise SystemExit(
            f"HOLD: Product request failed (HTTP {response.status_code})."
        )

    result = response.json()

    if result.get("errors"):
        for error in result["errors"]:
            print("Shopify error:", error.get("message"))
        raise SystemExit("HOLD: Could not read the product.")

    data = result["data"]
    variant = data["productVariant"]

    if variant is None:
        raise SystemExit("HOLD: Pilot variant was not found in this store.")

    print("Connected to:", data["shop"]["name"])
    print("Product:", variant["product"]["title"])

    for option in variant["selectedOptions"]:
        print(f"{option['name']}: {option['value']}")

    print("Variant ID:", variant["id"])
    print(f"Current price: {variant['price']} {data['shop']['currencyCode']}")
    print("Read-only check complete. No prices were changed.")

except requests.RequestException:
    raise SystemExit("Connection failed. Check your internet and store domain.")
except (ValueError, KeyError, TypeError):
    raise SystemExit("HOLD: Shopify returned an unexpected response.")