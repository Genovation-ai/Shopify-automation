import json
from urllib.request import urlopen

url = "https://shoptechgenius.ca/products/apple-iphone-12-64gb.js"

with urlopen(url, timeout=15) as response:
    product = json.load(response)

print("Product:", product["title"])
print("Options:", [option["name"] for option in product["options"]])

for variant in product["variants"]:
    details = " | ".join(str(option) for option in variant["options"])

    if "64gb" in details.lower() and "black" in details.lower() and "excellent" in details.lower():
        print("Matching variant:", details)
        print("Variant ID:", variant["id"])
        print(f"Store price: ${variant['price'] / 100:.2f}")