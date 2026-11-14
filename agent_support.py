"""Shared local catalogue, approval and report helpers. No network access."""
import ast
import csv
import json
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

SOURCE_CONDITIONS = {
    "Amazon.ca": {"Excellent"},
    "BestBuy.ca": {"Good", "Fair"},
    "DERIVED_MIDPOINT": {"Very Good"},
}
ALL_CONDITIONS = {"Excellent", "Very Good", "Good", "Fair"}


def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def search_model_name(model):
    # Only this reviewed catalogue label is an alias. Retain the original
    # identity in approvals, Shopify validation, proposals and fallback groups.
    return "iPhone 13" if model == "iPhone 13 - Christmas Deal!" else model


def model_matches(title, target):
    normalize = lambda value: " ".join(value.casefold().split())
    models = re.findall(
        r"\biphone\s+(?:\d{1,2}e?|se|xr|xs|x)"
        r"(?:\s+pro(?:\s+max)?|\s+plus|\s+mini|\s+max)?\b",
        title, re.IGNORECASE,
    )
    if len({normalize(m) for m in models}) != 1:
        return False
    model = normalize(models[0])
    wanted = normalize(search_model_name(target))
    if wanted in {"iphone se 2nd generation", "iphone se 3rd generation"}:
        if model != "iphone se":
            return False
        generations = set()
        for pattern, generation in (
            (r"\b(?:1st|first)\s+(?:gen|generation)\b|\b2016\b", 1),
            (r"\b(?:2nd|second)\s+(?:gen|generation)\b|\b2020\b", 2),
            (r"\b(?:3rd|third)\s+(?:gen|generation)\b|\b2022\b", 3),
        ):
            if re.search(pattern, title, re.IGNORECASE):
                generations.add(generation)
        expected = 2 if "2nd" in wanted else 3
        return generations == {expected}
    return model == wanted


def replace_with_retry(temporary, path, attempts=8, delay=0.5):
    """Retry only the local rename, never an external price update."""
    for attempt in range(attempts):
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            if attempt == 0:
                print(f"File busy: {path.name}; retrying local save.", flush=True)
            if attempt == attempts - 1:
                raise PermissionError(
                    f"Cannot save {path.name}. Close Excel and preview windows, "
                    f"and pause OneDrive. Pending data remains in {temporary.name}. "
                    "The agent has stopped; check the audit log and live prices "
                    "before retrying an apply run."
                ) from error
            time.sleep(delay)


def write_rows(path, records, fields):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    replace_with_retry(temporary, path)


def fresh(value, started=None):
    checked = datetime.fromisoformat(value)
    if checked.tzinfo is None:
        raise ValueError("Result timestamp has no timezone.")
    age = datetime.now().astimezone() - checked
    if not -timedelta(minutes=5) <= age <= timedelta(minutes=60):
        raise ValueError("Result is stale. Run fresh verification.")
    if started and checked < started - timedelta(seconds=1):
        raise ValueError("Result is from a previous run.")


def target_key(row):
    return (row["variant_id"], row["product_title"], row["storage"],
            row["color"], row["condition"])


def offer_key(row):
    return (row["shopify_variant_id"], row["model"], row["storage"],
            row["color"], row["condition"])


def candidate_key(row):
    return (row["shopify_variant_id"], row["target_model"], row["target_storage"],
            row["target_color"], row["target_condition"])


def catalogue(folder, model, source=None, condition=None):
    targets = []
    for r in read_rows(folder / "competitor_searches.csv"):
        if r.get("product_title") != model:
            continue
        row_source = r.get("competitor_source", "")
        row_cond = r.get("condition", "")
        if source and row_source != source:
            continue
        if condition and row_cond != condition:
            continue
        if row_cond not in SOURCE_CONDITIONS.get(row_source, set()):
            continue
        targets.append(r)

    seen = set()
    for row in targets:
        if not re.fullmatch(r"gid://shopify/ProductVariant/\d+", row["variant_id"]):
            raise ValueError("Invalid catalogue variant ID.")
        if row["variant_id"] in seen:
            raise ValueError("Duplicate catalogue variant ID: " + row["variant_id"])
        seen.add(row["variant_id"])
    return targets


def legacy_ids(folder):
    """Read the existing approved set without importing any Shopify script."""
    tree = ast.parse((folder / "batch_update.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "allowed_ids" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise ValueError("Existing approval set not found.")


def approval_records(folder):
    path = folder / "approved_targets.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("targets"), list):
        raise ValueError("Invalid approved_targets.json.")
    records = data["targets"]
    for r in records:
        if r.get("condition") not in ALL_CONDITIONS or not re.fullmatch(
            r"gid://shopify/ProductVariant/\d+", r["variant_id"]
        ) or not all(r.get(k) for k in ("model", "storage", "color")):
            raise ValueError("Invalid approved target.")
    if len({r["variant_id"] for r in records}) != len(records):
        raise ValueError("Duplicate approved target ID.")
    return records


def is_approved(proposal, existing_ids, records):
    # Explicit metadata takes precedence, including for a previously approved ID.
    entries = [r for r in records if r["variant_id"] == proposal["variant_id"]]
    if entries:
        return all(entries[0][k] == proposal[k]
                   for k in ("model", "storage", "color", "condition"))
    return proposal["variant_id"] in existing_ids


def approve_targets(folder, targets):
    existing = {r["variant_id"]: r for r in approval_records(folder)}
    for t in targets:
        record = {"variant_id": t["variant_id"], "model": t["product_title"],
                  "storage": t["storage"], "color": t["color"], "condition": t["condition"]}
        if record["variant_id"] in existing and existing[record["variant_id"]] != record:
            raise ValueError("Approval metadata changed; review approved_targets.json.")
        existing[record["variant_id"]] = record
    path = folder / "approved_targets.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"schema_version": 1, "targets": list(existing.values())},
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


REPORT_FIELDS = ["model", "storage", "color", "condition", "variant_id", "status",
                 "reason", "candidate_count", "verified_count", "proposed_price_cad",
                 "pricing_basis", "source_color", "source_variant_id"]


def choose_offer(target, offers):
    """Prefer the exact variant; otherwise lowest same-model/storage/grade colour.

    Caller supplies only fresh, VERIFIED, identity-checked source offers from
    the current selection. No proposal or fallback can become a source offer.
    """
    peers = [o for o in offers if o["status"] == "VERIFIED"
             and (o["model"], o["storage"], o["condition"]) ==
                 (target["product_title"], target["storage"], target["condition"])]
    exact = [o for o in peers if o["shopify_variant_id"] == target["variant_id"]
             and o["color"] == target["color"]]
    other = [o for o in peers if o["color"] != target["color"]
             and o["shopify_variant_id"] != target["variant_id"]]
    pool = exact or other
    if not pool:
        return None
    source = min(pool, key=lambda o: (Decimal(o["price_cad"]), o["color"], o["source_url"]))
    return source, "EXACT_COLOUR" if exact else "COLOUR_FALLBACK"


def report_rows(targets, candidates, offers, proposals, updates, state, reason=""):
    output = []
    for target in targets:
        key = target_key(target)
        cs = [c for c in candidates if candidate_key(c) == key]
        os = [o for o in offers if offer_key(o) == key]
        verified = [o for o in os if o["status"] == "VERIFIED"]
        ps = [p for p in proposals if (p["variant_id"], p["model"], p["storage"],
              p["color"], p["condition"]) == key]
        update = next((u for u in updates if u["variant_id"] == key[0]), None)
        status, detail = state, reason
        if update:
            status, detail = update["status"], update.get("reason", "")
        elif state in {"ERROR", "INTERRUPTED"}:
            pass  # A failed search is never reported as a successful no-match.
        elif ps:
            status, detail = "PREVIEW", ""
        elif not cs:
            status, detail = "NO_CANDIDATE", "No eligible title candidate in this search."
        elif not verified:
            status = "HOLD"
            detail = " | ".join(dict.fromkeys(o.get("reason", "") for o in os)) or "No verified offer."
        elif not ps:
            status, detail = "HOLD", reason or "Verified offer did not produce a proposal."
        p = ps[0] if ps else {}
        if p.get("pricing_basis") == "COLOUR_FALLBACK":
            detail = (detail + " | " if detail else "") + "Colour fallback from " + p["source_color"]
        output.append(dict(model=key[1], storage=key[2], color=key[3], condition=key[4],
                           variant_id=key[0], status=status, reason=detail,
                           candidate_count=len(cs), verified_count=len(verified),
                           proposed_price_cad=p.get("proposed_price_cad", ""),
                           pricing_basis=p.get("pricing_basis", ""),
                           source_color=p.get("source_color", ""),
                           source_variant_id=p.get("source_variant_id", "")))
    return output