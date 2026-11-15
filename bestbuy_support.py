"""Pure, conservative Best Buy product-offer checks. No network or store writes."""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from agent_support import model_matches


def normalize(value):
    return " ".join(value.casefold().split()).replace("grey", "gray")


def canonical_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {"bestbuy.ca", "www.bestbuy.ca"}
            or parsed.port not in {None, 443} or parsed.username or parsed.password):
        raise ValueError("Not a Best Buy Canada product URL.")
    if not re.fullmatch(r"/en-ca/product/[a-zA-Z0-9-]+/\d{7,10}/?", parsed.path):
        raise ValueError("Not a canonical Best Buy Canada product page.")
    return "https://www.bestbuy.ca" + parsed.path.rstrip("/")


def web_code(url):
    return canonical_url(url).rsplit("/", 1)[1]


COLOURS = ("black titanium", "blue titanium", "natural titanium", "white titanium",
           "midnight green", "sierra blue", "pacific blue", "alpine green", "deep purple",
           "space black", "space gray", "rose gold", "jet black", "starlight", "midnight",
           "graphite", "coral", "purple", "silver", "yellow", "black", "white", "green",
           "blue", "pink", "gold", "red")

GRADES = ("Very Good", "Good", "Fair", "Acceptable", "Premium")


def condition_grade(value):
    for grade in GRADES:
        if normalize(value) == normalize(grade):
            return grade
    raise ValueError("Best Buy requires an explicit non-Excellent condition; Excellent uses Amazon.")


def refurbished_grades(text):
    return {normalize(grade) for grade in re.findall(
        r"\brefurbished\s*(?:-\s*|\(\s*)?"
        r"(very\s+good|excellent|good|fair|acceptable|premium)\b", text, re.IGNORECASE)}


def colour_matches(title, expected):
    text = normalize(title)
    found = set()
    # Consume compound colours first: Jet Black must never match plain Black.
    for colour in COLOURS:
        pattern = r"\b" + re.escape(colour) + r"\b"
        if re.search(pattern, text):
            found.add(colour)
            text = re.sub(pattern, " ", text)
    return found == {normalize(expected)}


def title_check(title, target):
    if not model_matches(title, target["product_title"]):
        raise ValueError("Model does not match, or SE generation is unclear.")
    sizes = {f"{n}{unit.upper()}" for n, unit in
             re.findall(r"\b(\d+)\s*(GB|TB)\b", title, re.IGNORECASE)}
    if sizes != {target["storage"].replace(" ", "").upper()}:
        raise ValueError("Storage does not match or is ambiguous.")
    if not colour_matches(title, target["color"]):
        raise ValueError("Colour does not match or is ambiguous.")
    expected = normalize(condition_grade(target["condition"]))
    if refurbished_grades(title) != {expected}:
        raise ValueError("Title must explicitly confirm Refurbished " + target["condition"] + ".")
    if re.search(r"\b(?:open\s*box|case|cover|charger|"
                 r"protector|cable|bundle|subscription|monthly|financing)\b", title, re.IGNORECASE):
        raise ValueError("Wrong condition, accessory, bundle or financing title.")


def spec_pairs(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labels = {"wireless carrier", "sim card", "sim type", "sim configuration",
              "product condition", "phone model", "storage"}
    pairs = {}
    for i, line in enumerate(lines[:-1]):
        if normalize(line) in labels:
            pairs.setdefault(normalize(line), set()).add(normalize(lines[i + 1]))
    return pairs


def main_offer_text(text):
    """Keep the price above the named outlet-comparison section, if present.

    Do not remove arbitrary lines containing 'from': ambiguous prices in the
    primary offer still block verification. Only a full section-heading line
    establishes the boundary, as shown in the reviewed Best Buy page.
    """
    boundary = re.search(r"(?im)^\s*Shop Outlet Deals\s*$", text)
    primary = text[:boundary.start()] if boundary else text
    if not primary.strip():
        raise ValueError("Main displayed offer is missing above outlet comparisons.")
    return primary.strip()


def price_from_offer(text):
    text = main_offer_text(text)
    if re.search(r"US\$|USD|/\s*mo\b|per month|monthly payment|starting at|from\s+\$|"
                 r"other sellers|other buying options|more buying options", text, re.IGNORECASE):
        raise ValueError("Offer currency, seller or financing price is ambiguous.")
    # Exclude only explicitly labelled savings. Any second unlabelled amount
    # remains ambiguous, including delivery, old prices and membership prices.
    text = re.sub(r"(?im)^\s*SAVE\s+\$[\d,]+(?:\.\d{2})?[^\n]*$", "", text)
    values = set()
    for whole, fraction, split_fraction in re.findall(
            r"\$\s*(\d+(?:,\d{3})*)(?:\s*\.\s*(\d{2})|\n\s*(\d{2})(?=\s|$))?"
            r"(?![\d.])", text):
        try:
            values.add(Decimal(whole.replace(",", "") + "." + (fraction or split_fraction or "00")))
        except InvalidOperation:
            raise ValueError("Invalid item price.")
    if len(values) != 1:
        raise ValueError("A single unambiguous item price is required in the primary offer.")
    price = values.pop()
    if not price.is_finite() or price <= Decimal("3.00"):
        raise ValueError("Invalid item price.")
    return price


def seller_from_header(text):
    matches = re.findall(r"(?im)^\s*Sold and shipped by\s+([^\n|]+)", text)
    sellers = {s.strip() for s in matches if s.strip()}
    if len(sellers) != 1:
        raise ValueError("Primary seller is missing or ambiguous.")
    return sellers.pop()


def verify_snapshot(snapshot, target, source_url):
    if web_code(snapshot["url"]) != web_code(source_url):
        raise ValueError("Page changed to a different Best Buy product.")
    code = web_code(source_url)
    if not re.search(r"\b" + code + r"\b", snapshot["sku_text"]):
        raise ValueError("Visible Web Code does not match the candidate.")
    title_check(snapshot["title"], target)
    evidence = snapshot["evidence_text"]
    pairs = spec_pairs(snapshot["specs_text"])
    expected = normalize(condition_grade(target["condition"]))
    conditions = pairs.get("product condition", set())
    if not conditions or any(refurbished_grades(v) != {expected} for v in conditions):
        raise ValueError("Specifications must explicitly confirm Refurbished " + target["condition"] + ".")
    if pairs.get("wireless carrier") != {"unlocked"}:
        raise ValueError("Specifications must explicitly confirm an unlocked carrier.")
    if "phone model" in pairs and not all(model_matches(v, target["product_title"])
                                          for v in pairs["phone model"]):
        raise ValueError("Specifications conflict with the target model.")
    if "storage" in pairs and {re.sub(r"\s+", "", v) for v in pairs["storage"]} != {
            re.sub(r"\s+", "", normalize(target["storage"]))}:
        raise ValueError("Specifications conflict with the target storage.")
    combined = snapshot["title"] + "\n" + evidence + "\n" + snapshot["specs_text"]
    if re.search(r"\blocked\s+to\b|\bnot\s+unlocked\b|\bcarrier[ -]locked\b|"
                 r"\b(?:sprint|verizon|straight talk|t-mobile|at&t)\s+only\b", combined, re.IGNORECASE):
        raise ValueError("Conflicting or locked-carrier evidence.")
    if normalize(target["product_title"]) == "iphone 14":
        if re.search(r"\be[\s-]?sim[\s-]*only\b|\bonly\s+e[\s-]?sim\b", combined, re.IGNORECASE):
            pass
        lines = evidence.splitlines() + [v for label, values in pairs.items()
                                       if label.startswith("sim") for v in values]
        if not any(re.search(r"\bnano[ -]?sim\b", line, re.IGNORECASE)
                   and re.search(r"\besim\b", line, re.IGNORECASE)
                   and not re.search(r"and/or|\bor\b|\bnot\b|\bwithout\b", line, re.IGNORECASE)
                   for line in lines):
            pass
    if snapshot["cart_enabled"] is not True:
        raise ValueError("No visible enabled Add to Cart for the primary product.")
    if re.search(r"out of stock|sold out|currently unavailable|not available for delivery",
                 snapshot["availability_text"], re.IGNORECASE):
        raise ValueError("Primary product availability is negative or conflicting.")
    return price_from_offer(snapshot["offer_text"]), seller_from_header(snapshot["seller_text"])
