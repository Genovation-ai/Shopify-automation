import argparse
import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from agent_support import ALL_CONDITIONS, approval_records, fresh, is_approved, read_rows, write_rows

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
parser.add_argument("--use-existing-proposals", action="store_true")
args = parser.parse_args()
folder = Path(__file__).resolve().parent
results = {}


def outcome(proposal, status, reason=""):
    results[proposal["variant_id"]] = {
        "variant_id": proposal["variant_id"], "status": status, "reason": reason,
    }
    write_rows(folder / "batch_update_results.csv", list(results.values()),
               ["variant_id", "status", "reason"])


write_rows(folder / "batch_update_results.csv", [], ["variant_id", "status", "reason"])

if args.use_existing_proposals:
    os.environ["SKIP_PROPOSAL_BUILD"] = "1"

# Rebuild proposals and read current Shopify prices (unless using pre-built midpoint proposals).
import batch_proposals as prepared

shopify = prepared.shopify
proposals = read_rows(folder / "batch_price_proposals.csv")
source_offers_by_id = {
    o["shopify_variant_id"]: o
    for o in read_rows(folder / "verified_offers.csv")
    if o.get("status") == "VERIFIED"
}

# Preserve previously approved IDs. New approvals live in approved_targets.json.
allowed_ids = {
    "gid://shopify/ProductVariant/41514825842735",  # Blue
    "gid://shopify/ProductVariant/41514825875503",  # Purple
    "gid://shopify/ProductVariant/41514825908271",  # Green
    "gid://shopify/ProductVariant/41514825941039",  # White
    "gid://shopify/ProductVariant/41514825973807",  # Black
    "gid://shopify/ProductVariant/41514828824623",  # iPhone 13 Blue
    "gid://shopify/ProductVariant/41514828857391",  # iPhone 13 Pink
    "gid://shopify/ProductVariant/41514828890159",  # iPhone 13 Starlight
    "gid://shopify/ProductVariant/41514828922927",  # iPhone 13 Green 128GB
    "gid://shopify/ProductVariant/41514828955695",  # iPhone 13 Red 128GB
    "gid://shopify/ProductVariant/41514828988463",  # iPhone 13 Blue 256GB
    "gid://shopify/ProductVariant/41514829021231",  # iPhone 13 Pink 256GB
    "gid://shopify/ProductVariant/41514829053999",  # iPhone 13 Starlight 256GB
    "gid://shopify/ProductVariant/41514829086767",  # iPhone 13 Green 256GB
    "gid://shopify/ProductVariant/41514829119535",  # iPhone 13 Red 256GB
    "gid://shopify/ProductVariant/41514829152303",  # iPhone 13 Blue 512GB
    "gid://shopify/ProductVariant/41514829185071",  # iPhone 13 Pink 512GB
    "gid://shopify/ProductVariant/41514829283375",  # iPhone 13 Red 512GB
    "gid://shopify/ProductVariant/41514829873199",  # iPhone 13 Midnight 128GB
    "gid://shopify/ProductVariant/41514829905967",  # iPhone 13 Midnight 256GB
    "gid://shopify/ProductVariant/41514829938735",  # iPhone 13 Midnight 512GB
    "gid://shopify/ProductVariant/41514829217839",  # iPhone 13 Starlight 512GB
    "gid://shopify/ProductVariant/41514826006575",  # iPhone 12 Red 64GB
    "gid://shopify/ProductVariant/41514826039343",  # iPhone 12 Blue 128GB
    "gid://shopify/ProductVariant/41514826072111",  # iPhone 12 Purple 128GB
    "gid://shopify/ProductVariant/41514826104879",  # iPhone 12 Green 128GB
    "gid://shopify/ProductVariant/41514826137647",  # iPhone 12 White 128GB
    "gid://shopify/ProductVariant/41514826170415",  # iPhone 12 Black 128GB
    "gid://shopify/ProductVariant/41514826203183",  # iPhone 12 Red 128GB
    "gid://shopify/ProductVariant/41514826235951",  # iPhone 12 Blue 256GB
    "gid://shopify/ProductVariant/41514826268719",  # iPhone 12 Purple 256GB
    "gid://shopify/ProductVariant/41514826301487",  # iPhone 12 Green 256GB
    "gid://shopify/ProductVariant/41514826334255",  # iPhone 12 White 256GB
    "gid://shopify/ProductVariant/41514826367023",  # iPhone 12 Black 256GB
    "gid://shopify/ProductVariant/41514826399791",  # iPhone 12 Red 256GB
    "gid://shopify/ProductVariant/41649643159599",  # iPhone 11 Black 64GB
    "gid://shopify/ProductVariant/41649643290671",  # iPhone 11 Green 64GB
    "gid://shopify/ProductVariant/41649643552815",  # iPhone 11 Purple 64GB
    "gid://shopify/ProductVariant/41649643683887",  # iPhone 11 Red 64GB
    "gid://shopify/ProductVariant/41649643814959",  # iPhone 11 White 64GB
    "gid://shopify/ProductVariant/41649644077103",  # iPhone 11 Green 128GB
    "gid://shopify/ProductVariant/41649644208175",  # iPhone 11 Yellow 128GB
    "gid://shopify/ProductVariant/41649644339247",  # iPhone 11 Purple 128GB
    "gid://shopify/ProductVariant/41649644470319",  # iPhone 11 Red 128GB
    "gid://shopify/ProductVariant/41649644601391",  # iPhone 11 White 128GB
    "gid://shopify/ProductVariant/41649644732463",  # iPhone 11 Black 256GB
    "gid://shopify/ProductVariant/41649644863535",  # iPhone 11 Green 256GB
    "gid://shopify/ProductVariant/41649644994607",  # iPhone 11 Yellow 256GB
    "gid://shopify/ProductVariant/41649645125679",  # iPhone 11 Purple 256GB
    "gid://shopify/ProductVariant/41649645256751",  # iPhone 11 Red 256GB
    "gid://shopify/ProductVariant/41649645387823",  # iPhone 11 White 256GB
    "gid://shopify/ProductVariant/41650108432431",  # iPhone 11 Pro Space Gray 64GB
    "gid://shopify/ProductVariant/41650108694575",  # iPhone 11 Pro Silver 64GB
    "gid://shopify/ProductVariant/41650109087791",  # iPhone 11 Pro Midnight Green 256GB
    "gid://shopify/ProductVariant/41650108825647",  # iPhone 11 Pro 64GB Gold
    "gid://shopify/ProductVariant/41650108956719",  # iPhone 11 Pro 256GB Space Gray
    "gid://shopify/ProductVariant/41650109349935",  # iPhone 11 Pro 256GB Gold
    "gid://shopify/ProductVariant/41650109481007",  # iPhone 11 Pro 512GB Space Gray
    "gid://shopify/ProductVariant/41650109612079",  # iPhone 11 Pro 512GB Midnight Green
    "gid://shopify/ProductVariant/41669481365551",  # iPhone 11 Pro Max 64GB Silver
    "gid://shopify/ProductVariant/41669481496623",  # iPhone 11 Pro Max 64GB Gold
    "gid://shopify/ProductVariant/41669481627695",  # iPhone 11 Pro Max 64GB Midnight Green
    "gid://shopify/ProductVariant/41669481758767",  # iPhone 11 Pro Max 64GB Space Gray
    "gid://shopify/ProductVariant/41669481889839",  # iPhone 11 Pro Max 256GB Silver
    "gid://shopify/ProductVariant/41669482020911",  # iPhone 11 Pro Max 256GB Gold
    "gid://shopify/ProductVariant/41669482151983",  # iPhone 11 Pro Max 256GB Midnight Green
    "gid://shopify/ProductVariant/41669482283055",  # iPhone 11 Pro Max 256GB Space Gray
    "gid://shopify/ProductVariant/41669482676271",  # iPhone 11 Pro Max 512GB Midnight Green
    "gid://shopify/ProductVariant/41669482807343",  # iPhone 11 Pro Max 512GB Space Gray
    "gid://shopify/ProductVariant/41643040309295",  # iPhone 12 Pro 256GB Silver
    "gid://shopify/ProductVariant/41643040342063",  # iPhone 12 Pro 256GB Graphite
    "gid://shopify/ProductVariant/41643040374831",  # iPhone 12 Pro 256GB Gold
    "gid://shopify/ProductVariant/41643040538671",  # iPhone 12 Pro 128GB Pacific Blue
    "gid://shopify/ProductVariant/41643040636975",  # iPhone 12 Pro 512GB Gold
    "gid://shopify/ProductVariant/41643040669743",  # iPhone 12 Pro 512GB Pacific Blue
    "gid://shopify/ProductVariant/41648357507119",  # iPhone 12 Pro Max 128GB Graphite
    "gid://shopify/ProductVariant/41648357638191",  # iPhone 12 Pro Max 128GB Gold
    "gid://shopify/ProductVariant/41648358031407",  # iPhone 12 Pro Max 256GB Graphite
    "gid://shopify/ProductVariant/41648358162479",  # iPhone 12 Pro Max 256GB Gold
    "gid://shopify/ProductVariant/41648357900335",  # iPhone 12 Pro Max 128GB Silver
    "gid://shopify/ProductVariant/41648358293551",  # iPhone 12 Pro Max 256GB Pacific Blue
    "gid://shopify/ProductVariant/41648358686767",  # iPhone 12 Pro Max 512GB Gold
}

mutation = """
mutation UpdatePrice(
  $productId: ID!,
  $variants: [ProductVariantsBulkInput!]!
) {
  productVariantsBulkUpdate(
    productId: $productId,
    variants: $variants
  ) {
    productVariants { id price }
    userErrors { field message }
  }
}
"""


def api(query, variables):
    response = requests.post(
        f"https://{shopify.domain}/admin/api/2026-10/graphql.json",
        headers={"X-Shopify-Access-Token": shopify.token},
        json={"query": query, "variables": variables},
        timeout=30,
        allow_redirects=False,
    )

    if response.status_code != 200:
        raise ValueError(f"Shopify returned HTTP {response.status_code}")

    result = response.json()
    if result.get("errors"):
        raise ValueError(str(result["errors"]))

    return result["data"]


def audit(proposal, status):
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "variant_id": proposal["variant_id"],
        "old_price": proposal["current_shopify_price_cad"],
        "new_price": proposal["proposed_price_cad"],
        "source_url": proposal["source_url"],
        "pricing_basis": proposal.get("pricing_basis", "EXACT_COLOUR"),
        "source_color": proposal.get("source_color", proposal["color"]),
        "source_variant_id": proposal.get("source_variant_id", proposal["variant_id"]),
        "offer_checked_at": proposal["offer_checked_at"],
        "status": status,
    }

    with (folder / "price_update_log.jsonl").open(
        "a", encoding="utf-8"
    ) as file:
        file.write(json.dumps(entry) + "\n")


def validate_live(proposal):
    variant = api(prepared.query, {"id": proposal["variant_id"]})["productVariant"]
    if variant is None:
        raise ValueError("Variant no longer exists.")
    options = {item["name"].casefold(): item["value"] for item in variant["selectedOptions"]}
    if (
        variant["id"] != proposal["variant_id"]
        or variant["product"]["id"] != proposal["product_id"]
        or variant["product"]["title"] != proposal["model"]
        or variant["product"]["status"] != "ACTIVE"
        or options.get("size", options.get("storage")) != proposal["storage"]
        or options.get("color", options.get("colour")) != proposal["color"]
        or options.get("condition") != proposal["condition"]
        or Decimal(variant["price"]) != Decimal(proposal["current_shopify_price_cad"])
    ):
        raise ValueError("Shopify details changed. Run a new preview.")
    if proposal.get("pricing_basis") == "COLOUR_FALLBACK":
        source = source_offers_by_id[proposal["source_variant_id"]]
        if (source["status"] != "VERIFIED"
            or source["shopify_variant_id"] != proposal["source_variant_id"]
            or source["color"] != proposal["source_color"]
            or source["color"] == proposal["color"]
            or source["shopify_variant_id"] == proposal["variant_id"]
            or (source["model"], source["storage"], source["condition"]) !=
                (proposal["model"], proposal["storage"], proposal["condition"])
            or source["checked_at"] != proposal["offer_checked_at"]
            or source["source_url"] != proposal["source_url"]
            or source["seller"] != proposal["seller"]
            or Decimal(source["price_cad"]) != Decimal(proposal["competitor_price_cad"])):
            raise ValueError("Invalid colour-fallback provenance.")
        source_variant = prepared.validate_source(source)
        if source_variant["product"]["id"] != proposal["product_id"]:
            raise ValueError("Source colour belongs to a different Shopify product.")


try:
    if shopify.domain != "shop-techgenius.myshopify.com":
        raise ValueError("Unexpected Shopify store.")

    changes = []
    approved = approval_records(folder)
    if shopify.data["shop"]["currencyCode"] != "CAD":
        raise ValueError("Unexpected Shopify currency.")
    if len({p["variant_id"] for p in proposals}) != len(proposals):
        raise ValueError("Duplicate proposal variant ID.")

    # Validate every proposal before sending any updates.
    for proposal in proposals:
        fresh(proposal["offer_checked_at"])

        target = Decimal(proposal["proposed_price_cad"])
        competitor = Decimal(proposal["competitor_price_cad"])
        is_estimated = proposal.get("pricing_basis") == "ESTIMATED_GRADE_PRICE"

        if (
            not target.is_finite()
            or not competitor.is_finite()
            or target <= 0
            or (not is_estimated and target != competitor - Decimal("3.00"))
            or (is_estimated and target != competitor)
            or proposal["condition"] not in ALL_CONDITIONS
        ):
            raise ValueError("Invalid pricing proposal.")

        validate_live(proposal)

        if proposal["action"] == "NO CHANGE":
            if target != Decimal(proposal["current_shopify_price_cad"]):
                raise ValueError("Invalid NO CHANGE proposal.")
            print("SKIPPED: Already correct —", proposal["color"])
            outcome(proposal, "ALREADY_CORRECT")
            continue
        if not is_approved(proposal, allowed_ids, approved):
            reason = "Target identity is not approved. Use run_agent.py --approve-targets for the reviewed selection."
            outcome(proposal, "HOLD_APPROVAL", reason)
            print(f"HOLD APPROVAL: {proposal['model']} {proposal['storage']} {proposal['color']} | {proposal['variant_id']}")
            continue
        changes.append(proposal)
        outcome(proposal, "NOT_APPLIED" if args.apply else "PREVIEW", "Validated; awaiting update." if args.apply else "")

    print(f"\nVariants requiring changes: {len(changes)}")

    if not args.apply:
        print("PREVIEW ONLY. No Shopify prices changed.")
        raise SystemExit(0)

    for proposal in changes:
        # Recheck freshness immediately before each write.
        fresh(proposal["offer_checked_at"])
        validate_live(proposal)

        audit(proposal, "UPDATE REQUEST STARTING")
        outcome(proposal, "UPDATE_UNCONFIRMED", "Update is starting; consult audit log if interrupted. Do not retry blindly.")

        data = api(
            mutation,
            {
                "productId": proposal["product_id"],
                "variants": [{
                    "id": proposal["variant_id"],
                    "price": proposal["proposed_price_cad"],
                }],
            },
        )

        update = data["productVariantsBulkUpdate"]
        if update["userErrors"]:
            audit(proposal, "SHOPIFY REJECTED UPDATE")
            outcome(proposal, "REJECTED", str(update["userErrors"]))
            raise ValueError(str(update["userErrors"]))

        updated = update["productVariants"]
        if (
            len(updated) != 1
            or updated[0]["id"] != proposal["variant_id"]
            or Decimal(updated[0]["price"])
            != Decimal(proposal["proposed_price_cad"])
        ):
            raise ValueError("Unexpected update response.")

        # Read the price back independently.
        verified = api(
            prepared.query,
            {"id": proposal["variant_id"]},
        )["productVariant"]

        if (
            verified is None
            or Decimal(verified["price"])
            != Decimal(proposal["proposed_price_cad"])
        ):
            raise ValueError("Read-back did not confirm the new price.")

        audit(proposal, "APPLIED AND VERIFIED")
        outcome(proposal, "APPLIED_AND_VERIFIED")
        print(
            f"UPDATED AND VERIFIED: {proposal['color']} "
            f"${proposal['proposed_price_cad']} CAD"
        )

    print("Batch complete. Audit saved to price_update_log.jsonl.")

except (requests.RequestException, ValueError, KeyError, TypeError, OSError, InvalidOperation) as error:
    raise SystemExit(
        f"HOLD: {error}\n"
        "If running with --apply, some updates may have completed. "
        "Check Shopify and the audit log before retrying."
    )