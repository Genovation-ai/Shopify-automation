import re

title = "Apple iPhone 12, 64GB, Black - Unlocked (Renewed, Excellent)"
text = title.lower()

model_match = re.search(r"\biphone\s+12(?:\s+pro(?:\s+max)?|\s+mini)?\b", text)
storage_match = re.search(r"\b(\d+)\s*gb\b", text)

listing = {
    "model": model_match.group(0).title() if model_match else None,
    "storage_gb": int(storage_match.group(1)) if storage_match else None,
    "color": "Black" if "black" in text else None,
    "condition": "Excellent" if "excellent" in text else None,
}

print("Extracted details:", listing)

if None in listing.values():
    print("NEEDS REVIEW: the title does not confirm every required detail.")
else:
    print("Ready for the product matching check.")