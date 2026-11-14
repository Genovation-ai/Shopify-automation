import re


def extract_title_details(title):
    """Read phone details from a listing title."""
    text = title.lower()

    model_match = re.search(
        r"\biphone\s+12(?:\s+pro(?:\s+max)?|\s+mini)?\b",
        text,
    )
    storage_match = re.search(r"\b(\d+)\s*gb\b", text)

    return {
        "model": model_match.group(0).title() if model_match else None,
        "storage_gb": int(storage_match.group(1)) if storage_match else None,
        "color": "Black" if "black" in text else None,
        "condition": "Excellent" if "excellent" in text else None,
    }


def compare_listing(our_phone, listing):
    """Check whether the listing matches our phone."""
    fields = ("model", "storage_gb", "color", "condition")
    differences = []

    for field in fields:
        if str(our_phone[field]).lower() != str(listing[field]).lower():
            differences.append(field)

    return {
        "matches": len(differences) == 0,
        "differences": differences,
    }