"""Non-interactive twice-daily orchestrator for Amazon.ca + BestBuy.ca + Very Good midpoint -> Shopify."""
import argparse
import subprocess
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from agent_support import (
    REPORT_FIELDS,
    approve_targets,
    read_rows,
    report_rows,
    write_rows,
)
from amazon_auto import OFFER_FIELDS, process_targets as run_amazon
from bestbuy_auto import process_targets as run_bestbuy
from batch_proposals import FIELDS as PROPOSAL_FIELDS, read_variant, validate_identity

folder = Path(__file__).resolve().parent
STEP_FACTOR = Decimal("0.95")  # 5% step-down per condition grade when capped


def q2(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def refresh_catalogue():
    for script in ("shopify_catalog.py", "catalog_check.py", "prepare_searches.py"):
        res = subprocess.run([sys.executable, "-u", str(folder / script)], cwd=folder)
        if res.returncode != 0:
            raise RuntimeError(f"{script} failed with exit code {res.returncode}")


def cap_proposals_below_upper_grades(proposals, condition, grade_prices):
    """Ensure Good and Fair never exceed higher condition grades (5% step-down per tier)."""
    modified = False
    for p in proposals:
        key = (p["model"], p["storage"], p["color"])
        proposed = Decimal(p["proposed_price_cad"])
        current = Decimal(p["current_shopify_price_cad"])
        exc_info = grade_prices.get((key[0], key[1], key[2], "Excellent"))
        vg_info = grade_prices.get((key[0], key[1], key[2], "Very Good"))
        good_info = grade_prices.get((key[0], key[1], key[2], "Good"))

        cap_price = None
        cap_note = ""

        if condition == "Good" and exc_info:
            exc_price = Decimal(exc_info["price"])
            vg_target = q2(exc_price * STEP_FACTOR)
            max_good = q2(vg_target * STEP_FACTOR)
            if proposed >= exc_price or proposed > max_good:
                cap_price = max_good
                cap_note = f"Capped -5% below Very Good (Exc=${exc_price:.2f})"
        elif condition == "Fair":
            if good_info:
                good_price = Decimal(good_info["price"])
                max_fair = q2(good_price * STEP_FACTOR)
                if proposed >= good_price:
                    cap_price = max_fair
                    cap_note = f"Capped -5% below Good (${good_price:.2f})"
            elif vg_info:
                vg_price = Decimal(vg_info["price"])
                max_fair = q2(q2(vg_price * STEP_FACTOR) * STEP_FACTOR)
                if proposed >= vg_price:
                    cap_price = max_fair
                    cap_note = f"Capped below Very Good (${vg_price:.2f})"
            elif exc_info:
                exc_price = Decimal(exc_info["price"])
                max_fair = q2(q2(q2(exc_price * STEP_FACTOR) * STEP_FACTOR) * STEP_FACTOR)
                if proposed >= exc_price:
                    cap_price = max_fair
                    cap_note = f"Capped below Excellent (${exc_price:.2f})"

        if cap_price is not None and cap_price != proposed:
            change = cap_price - current
            action = "INCREASE" if change > 0 else "DECREASE" if change < 0 else "NO CHANGE"
            print(
                f"[{condition.upper()} CAP] {p['model']} {p['storage']} {p['color']}: "
                f"Competitor proposal ${proposed:.2f} -> capped at ${cap_price:.2f} CAD ({cap_note})",
                flush=True,
            )
            p["proposed_price_cad"] = f"{cap_price:.2f}"
            p["competitor_price_cad"] = f"{cap_price:.2f}"
            p["discount_cad"] = "0.00"
            p["change_cad"] = f"{change:.2f}"
            p["action"] = action
            p["seller"] = f"{p['seller']} ({cap_note})"
            p["pricing_basis"] = "ESTIMATED_GRADE_PRICE"
            p["source_color"] = p["color"]
            p["source_variant_id"] = p["variant_id"]
            p["capped"] = "1"
            modified = True

    return modified


def build_very_good_proposals(vg_targets, grade_prices):
    """Compute Very Good = (Good + Excellent) / 2, or 5% below Excellent when Good was inverted/capped."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    proposals = []
    report = []

    for t in vg_targets:
        key = (t["product_title"], t["storage"], t["color"])
        good_info = grade_prices.get((key[0], key[1], key[2], "Good"))
        exc_info = grade_prices.get((key[0], key[1], key[2], "Excellent"))

        if not good_info or not exc_info:
            missing = []
            if not good_info:
                missing.append("Good")
            if not exc_info:
                missing.append("Excellent")
            reason = f"HOLD: Missing fresh {' & '.join(missing)} price for exact colour {t['color']}."
            print(f"[VERY GOOD HOLD] {t['product_title']} {t['storage']} {t['color']}: {reason}", flush=True)
            report.append(dict(
                model=t["product_title"], storage=t["storage"], color=t["color"],
                condition="Very Good", variant_id=t["variant_id"], status="HOLD",
                reason=reason, candidate_count=0, verified_count=0,
                proposed_price_cad="", pricing_basis="", source_color="", source_variant_id="",
            ))
            continue

        good_price = Decimal(good_info["price"])
        exc_price = Decimal(exc_info["price"])
        vg_cap = q2(exc_price * STEP_FACTOR)

        if good_info.get("capped") or good_price >= exc_price:
            est_price = vg_cap
            basis_note = f"5% below Excellent (${exc_price:.2f}); Good capped at ${good_price:.2f}"
        else:
            est_price = q2((good_price + exc_price) / Decimal("2"))
            if est_price >= exc_price:
                est_price = vg_cap
            basis_note = f"Good=${good_price:.2f}; Excellent=${exc_price:.2f}"

        variant = read_variant(t["variant_id"])
        validate_identity(variant, t["variant_id"], t["product_title"], t["storage"], t["color"], "Very Good")
        current = Decimal(variant["price"])
        change = est_price - current
        action = "INCREASE" if change > 0 else "DECREASE" if change < 0 else "NO CHANGE"

        proposals.append(dict(
            prepared_at=now,
            offer_checked_at=now,
            model=t["product_title"],
            storage=t["storage"],
            color=t["color"],
            condition="Very Good",
            product_id=variant["product"]["id"],
            variant_id=t["variant_id"],
            current_shopify_price_cad=f"{current:.2f}",
            competitor_price_cad=f"{est_price:.2f}",
            discount_cad="0.00",
            proposed_price_cad=f"{est_price:.2f}",
            change_cad=f"{change:.2f}",
            action=action,
            seller=basis_note,
            source_url=f"{good_info['url']} | {exc_info['url']}",
            status="PROPOSAL ONLY - NOT APPLIED",
            pricing_basis="ESTIMATED_GRADE_PRICE",
            source_color=t["color"],
            source_variant_id=f"{good_info['variant_id']}|{exc_info['variant_id']}",
        ))
        print(
            f"[VERY GOOD ESTIMATED] {t['product_title']} {t['storage']} {t['color']}: "
            f"${current:.2f} -> ${est_price:.2f} CAD ({basis_note})",
            flush=True,
        )
        report.append(dict(
            model=t["product_title"], storage=t["storage"], color=t["color"],
            condition="Very Good", variant_id=t["variant_id"], status="PREVIEW",
            reason=basis_note, candidate_count=0, verified_count=2,
            proposed_price_cad=f"{est_price:.2f}", pricing_basis="ESTIMATED_GRADE_PRICE",
            source_color=t["color"], source_variant_id=f"{good_info['variant_id']}|{exc_info['variant_id']}",
        ))

    return proposals, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply price changes to Shopify.")
    parser.add_argument("--approve-all", action="store_true", help="Auto-approve all active catalogue variants before running.")
    parser.add_argument("--models", nargs="+", help="Optional filter for specific iPhone models.")
    parser.add_argument("--skip-catalog-refresh", action="store_true", help="Reuse existing competitor_searches.csv.")
    args = parser.parse_args()

    if not args.skip_catalog_refresh:
        print("=== 1. Refreshing Shopify Catalogue ===", flush=True)
        refresh_catalogue()

    targets = read_rows(folder / "competitor_searches.csv")
    if args.models:
        wanted = set(args.models)
        targets = [t for t in targets if t["product_title"] in wanted]

    if not targets:
        raise SystemExit("HOLD: No eligible targets found in competitor_searches.csv.")

    if args.approve_all:
        approve_targets(folder, targets)
        print(f"Approved {len(targets)} variants in approved_targets.json.", flush=True)

    # Process in strict hierarchy order: Excellent -> Good -> Very Good -> Fair
    grade_order = {"Excellent": 0, "Good": 1, "Very Good": 2, "Fair": 3}
    groups = {}
    for t in targets:
        key = (t["product_title"], t["storage"], t["condition"])
        groups.setdefault(key, []).append(t)

    sorted_groups = sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], grade_order.get(item[0][2], 9)))

    all_report_rows = []
    grade_prices = {}  # (model, storage, color, condition) -> {"price": str, "url": str, "variant_id": str, "capped": bool}
    print(f"=== 2. Processing {len(targets)} variants across {len(sorted_groups)} groups ===", flush=True)

    for idx, ((model, storage, condition), group_targets) in enumerate(sorted_groups, 1):
        print(f"\n--- Group {idx}/{len(sorted_groups)}: {model} {storage} [{condition}] ({len(group_targets)} colours) ---", flush=True)
        write_rows(folder / "batch_targets.csv", group_targets, list(group_targets[0].keys()))

        if condition == "Very Good":
            vg_proposals, vg_report = build_very_good_proposals(group_targets, grade_prices)
            write_rows(folder / "batch_price_proposals.csv", vg_proposals, PROPOSAL_FIELDS)
            if vg_proposals:
                cmd = [sys.executable, "-u", str(folder / "batch_update.py"), "--use-existing-proposals"]
                if args.apply:
                    cmd.append("--apply")
                subprocess.run(cmd, cwd=folder)
                updates = {r["variant_id"]: r for r in read_rows(folder / "batch_update_results.csv")}
                for r in vg_report:
                    if r["variant_id"] in updates:
                        r["status"] = updates[r["variant_id"]]["status"]
                        if updates[r["variant_id"]]["reason"]:
                            r["reason"] += f" | {updates[r['variant_id']]['reason']}"
            for p in vg_proposals:
                grade_prices[(p["model"], p["storage"], p["color"], "Very Good")] = {
                    "price": p["proposed_price_cad"],
                    "url": p["source_url"],
                    "variant_id": p["variant_id"],
                    "capped": False,
                }
            all_report_rows.extend(vg_report)
            write_rows(folder / "catalogue_report.csv", all_report_rows, REPORT_FIELDS)
            continue

        source = group_targets[0]["competitor_source"]
        if source == "Amazon.ca":
            offers = run_amazon(group_targets)
        elif source == "BestBuy.ca":
            offers = run_bestbuy(group_targets)
        else:
            continue

        write_rows(folder / "verified_offers.csv", offers, OFFER_FIELDS)

        if any(o["status"] == "VERIFIED" for o in offers):
            prop_res = subprocess.run([sys.executable, "-u", str(folder / "batch_proposals.py")], cwd=folder)
            if prop_res.returncode == 0:
                proposals = read_rows(folder / "batch_price_proposals.csv")
                was_capped = cap_proposals_below_upper_grades(proposals, condition, grade_prices)
                if was_capped:
                    clean_proposals = [{k: p[k] for k in PROPOSAL_FIELDS} for p in proposals]
                    write_rows(folder / "batch_price_proposals.csv", clean_proposals, PROPOSAL_FIELDS)
                cmd = [sys.executable, "-u", str(folder / "batch_update.py"), "--use-existing-proposals"]
                if args.apply:
                    cmd.append("--apply")
                subprocess.run(cmd, cwd=folder)
        else:
            proposals = []

        for p in proposals:
            grade_prices[(p["model"], p["storage"], p["color"], p["condition"])] = {
                "price": p["proposed_price_cad"],
                "url": p["source_url"],
                "variant_id": p["variant_id"],
                "capped": p.get("capped") == "1",
            }

        updates = read_rows(folder / "batch_update_results.csv")
        state = "PREVIEW" if not args.apply else "HOLD"
        all_report_rows.extend(
            report_rows(group_targets, [], offers, proposals, updates, state)
        )
        write_rows(folder / "catalogue_report.csv", all_report_rows, REPORT_FIELDS)

    print(f"\n=== Run Finished at {datetime.now().astimezone().isoformat(timespec='seconds')} ===", flush=True)
    print("Summary saved to catalogue_report.csv and price_update_log.jsonl.")


if __name__ == "__main__":
    main()
