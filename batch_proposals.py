"""Prepare exact-colour or explicitly labelled colour-fallback proposals."""
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import requests
import shopify_check as shopify
from agent_support import (SOURCE_CONDITIONS, choose_offer, fresh, offer_key,
                           read_rows, target_key, write_rows)

folder = Path(__file__).resolve().parent
now = datetime.now().astimezone()
query = """
query CheckVariant($id: ID!) {
  productVariant(id: $id) {
    id price product { id title status }
    selectedOptions { name value }
  }
}
"""
FIELDS = ["prepared_at", "offer_checked_at", "model", "storage", "color", "condition",
          "product_id", "variant_id", "current_shopify_price_cad", "competitor_price_cad",
          "discount_cad", "proposed_price_cad", "change_cad", "action", "seller", "source_url",
          "status", "pricing_basis", "source_color", "source_variant_id"]

URL_PATTERNS = {
    "Amazon.ca": r"https://www\.amazon\.ca/dp/[A-Z0-9]{10}",
    "BestBuy.ca": r"https://www\.bestbuy\.ca/en-ca/product/[a-zA-Z0-9-]+/\d{7,10}",
}


def read_variant(identity):
    response = requests.post(
        f"https://{shopify.domain}/admin/api/2026-10/graphql.json",
        headers={"X-Shopify-Access-Token": shopify.token},
        json={"query": query, "variables": {"id": identity}},
        timeout=30, allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Shopify returned HTTP {response.status_code}")
    result = response.json()
    if result.get("errors"):
        raise ValueError(str(result["errors"]))
    variant = result["data"]["productVariant"]
    if variant is None:
        raise ValueError("Shopify variant not found: " + identity)
    return variant


def validate_identity(variant, identity, model, storage, color, condition):
    options = {item["name"].casefold(): item["value"] for item in variant["selectedOptions"]}
    if (variant["id"] != identity or variant["product"]["title"] != model
        or variant["product"]["status"] != "ACTIVE"
        or options.get("size", options.get("storage")) != storage
        or options.get("color", options.get("colour")) != color
        or options.get("condition") != condition):
        raise ValueError("Shopify identity does not match: " + identity)


def validate_source(offer):
    """Also called immediately before fallback writes by batch_update.py."""
    fresh(offer["checked_at"])
    variant = read_variant(offer["shopify_variant_id"])
    validate_identity(variant, offer["shopify_variant_id"], offer["model"], offer["storage"],
                      offer["color"], offer["condition"])
    return variant


proposals = []
source_offers = {}

if __name__ == "__main__":
    try:
        # Clear older proposals even when every current offer is on HOLD or stale.
        write_rows(folder / "batch_price_proposals.csv", [], FIELDS)
        if shopify.domain != "shop-techgenius.myshopify.com" or shopify.data["shop"]["currencyCode"] != "CAD":
            raise ValueError("Unexpected Shopify store or currency.")

        unified_path = folder / "verified_offers.csv"
        legacy_path = folder / "amazon_verified_offers.csv"
        offers = read_rows(unified_path) if unified_path.exists() else read_rows(legacy_path)

        targets = read_rows(folder / "batch_targets.csv")
        if not targets:
            # Backwards-compatible standalone exact-colour mode. No inferred targets.
            targets = list({o["shopify_variant_id"]: {
                "variant_id": o["shopify_variant_id"], "product_title": o["model"],
                "storage": o["storage"], "color": o["color"], "condition": o["condition"],
            } for o in offers}.values())
            allow_fallback = False
        else:
            allow_fallback = True
        keys = {target_key(t) for t in targets}
        if len({t["variant_id"] for t in targets}) != len(targets):
            raise ValueError("Duplicate target identity.")
        eligible = []
        checked_sources = {}
        for offer in offers:
            if offer_key(offer) not in keys:
                raise ValueError("Offer is outside the current target selection. Run run_agent.py again.")
            if offer["status"] != "VERIFIED":
                continue
            try:
                fresh(offer["checked_at"])
            except ValueError:
                print("SKIPPED: Stale offer for", offer["color"])
                continue
            store = offer.get("store", "")
            url_pattern = URL_PATTERNS.get(store)
            if (not url_pattern
                or offer.get("condition") not in SOURCE_CONDITIONS[store]
                or not offer.get("seller", "").strip()
                or not offer.get("product_title", "").strip()
                or not re.fullmatch(url_pattern, offer.get("source_url", ""))):
                raise ValueError("Unexpected verified source offer details.")
            price = Decimal(offer["price_cad"])
            if not price.is_finite() or price <= Decimal("3.00"):
                raise ValueError("Invalid source offer price.")
            source_id = offer["shopify_variant_id"]
            if source_id not in checked_sources:
                checked_sources[source_id] = validate_source(offer)
            eligible.append(offer)
        if not eligible:
            raise ValueError("No fresh verified source offers.")

        for target in targets:
            chosen = choose_offer(target, eligible)
            if not chosen:
                print(f"HOLD: {target['storage']} {target['color']} ({target['condition']}): no fresh matching source colour.")
                continue
            offer, basis = chosen
            if basis == "COLOUR_FALLBACK" and not allow_fallback:
                continue
            identity = target["variant_id"]
            variant = read_variant(identity)
            validate_identity(variant, identity, target["product_title"], target["storage"],
                              target["color"], target["condition"])
            source_variant = checked_sources[offer["shopify_variant_id"]]
            if source_variant["product"]["id"] != variant["product"]["id"]:
                raise ValueError("Source colour belongs to a different Shopify product.")
            current = Decimal(variant["price"])
            if not current.is_finite() or current < 0:
                raise ValueError("Invalid current Shopify price.")
            competitor = Decimal(offer["price_cad"])
            proposed = competitor - Decimal("3.00")
            change = proposed - current
            action = "INCREASE" if change > 0 else "DECREASE" if change < 0 else "NO CHANGE"
            proposal = dict(prepared_at=now.isoformat(timespec="seconds"), offer_checked_at=offer["checked_at"],
                model=target["product_title"], storage=target["storage"], color=target["color"],
                condition=target["condition"], product_id=variant["product"]["id"], variant_id=identity,
                current_shopify_price_cad=f"{current:.2f}", competitor_price_cad=f"{competitor:.2f}",
                discount_cad="3.00", proposed_price_cad=f"{proposed:.2f}", change_cad=f"{change:.2f}",
                action=action, seller=offer["seller"], source_url=offer["source_url"],
                status="PROPOSAL ONLY - NOT APPLIED", pricing_basis=basis,
                source_color=offer["color"], source_variant_id=offer["shopify_variant_id"])
            proposals.append(proposal)
            source_offers[identity] = offer
            print(f"{target['storage']} {target['color']} [{target['condition']}]: ${current:.2f} → ${proposed:.2f} CAD "
                  f"({action}, change {change:+.2f}; {basis}, source: {offer['store']} {offer['color']})")

        write_rows(folder / "batch_price_proposals.csv", proposals, FIELDS)
        print(f"\nSaved {len(proposals)} proposals to batch_price_proposals.csv")
        print("No Shopify prices were changed.")
    except requests.RequestException:
        raise SystemExit("HOLD: Shopify connection failed.")
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation) as error:
        raise SystemExit(f"HOLD: {error}")