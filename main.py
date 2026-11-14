import json
from urllib.request import urlopen
from listing_matcher import compare_listing, extract_title_details

# Shopify price is fetched automatically; Amazon price is entered after checking it.
our_phone = {
    "model": "iPhone 12",
    "storage_gb": 64,
    "condition": "Excellent",
    "color": "Black",
    "current_price": None,
    "pilot_acceptable_price": 312.99,
}

listings = [
    {
        "store": "Amazon.ca",
        "title": "Apple iPhone 12, 64GB, Black - Unlocked (Renewed)",
        "condition": input(
    "Condition grade you verified on Amazon (or press Enter if unclear): "
).strip().title() or None,
        "price": float(input("Enter the Amazon price you just checked (CAD): $")),
        "url": "https://www.amazon.ca/dp/B08PP5MSVB",
    },
]

if listings[0]["price"] <= 0:
    raise ValueError("Amazon price must be greater than zero.")

shopify_url = "https://shoptechgenius.ca/products/apple-iphone-12-64gb.js"

with urlopen(shopify_url, timeout=15) as response:
    product = json.load(response)

wanted_options = [
    f"{our_phone['storage_gb']}GB",
    our_phone["color"],
    our_phone["condition"],
]

variant = next(
    (item for item in product["variants"] if item["options"] == wanted_options),
    None,
)

if variant is None:
    raise ValueError(f"Shopify variant not found: {wanted_options}")

our_phone["current_price"] = variant["price"] / 100
print(f"Tech Genius variant ID: {variant['id']}")
print(f"Current Tech Genius price: ${our_phone['current_price']:.2f}")


matching_listings = []

for listing in listings:
    extracted = extract_title_details(listing["title"])
    listing["model"] = extracted["model"]
    listing["storage_gb"] = extracted["storage_gb"]
    listing["color"] = extracted["color"]
    comparison = compare_listing(our_phone, listing)
    matches = comparison["matches"]

    if matches:
        matching_listings.append(listing)
        print(f"MATCH: {listing['store']} — ${listing['price']:.2f}")
    else:
        print(
            f"SKIPPED: {listing['store']} — different "
            f"{', '.join(comparison['differences'])}"
        )

if matching_listings:
    cheapest = min(matching_listings, key=lambda listing: listing["price"])
    proposed_price = cheapest["price"] - 5

    print(f"\nCandidate price for review: ${proposed_price:.2f}")
    print(f"Based on: {cheapest['store']} at ${cheapest['price']:.2f}")
    print(f"Source: {cheapest['url']}")

    if round(proposed_price, 2) == our_phone["pilot_acceptable_price"]:
        print("Matches the acceptable pilot price. Shopify price has not been changed.")
    else:
        print("HOLD: This candidate price needs new approval.")
else:
    print("\nHOLD: no matching competitor listings found.")