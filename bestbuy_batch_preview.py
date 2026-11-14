"""Verify fresh Best Buy captures together. Offline, preview only, no Shopify access."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from bestbuy_preview import FIELDS, catalogue, save_snapshot, write_report
from bestbuy_support import title_check


def load_capture(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Capture file is too large.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1 or data.get("source") != "BESTBUY_VISIBLE_MAIN_OFFER":
        raise ValueError("Not a browser capture. Historical evidence and reports are not fresh inputs.")
    checked = datetime.fromisoformat(data["checked_at"].replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise ValueError("Capture timestamp has no timezone.")
    return data, checked


def batch(capture_dir, run, targets):
    groups = {t["variant_id"]: [] for t in targets}
    inputs = []
    # Only the known capture filename family, never credentials/approvals JSON.
    # Chrome's (1) suffix and accidental .json.json suffix are both accepted.
    for path in sorted(capture_dir.glob("bestbuy*.json")):
        audit = {"capture_file": path.name, "status": "HOLD", "reason": ""}
        inputs.append(audit)
        try:
            data, checked = load_capture(path)
            matches = []
            for target in targets:
                try:
                    title_check(data["snapshot"]["title"], target)
                    matches.append(target)
                except ValueError:
                    pass
            if len(matches) != 1:
                raise ValueError("Capture does not identify exactly one selected target; model, storage, colour and grade must match.")
            groups[matches[0]["variant_id"]].append((checked, path, data, audit))
            audit["variant_id"] = matches[0]["variant_id"]
            audit["status"] = "MATCHED"
        except Exception as error:
            audit["reason"] = str(error)

    records = []
    for index, target in enumerate(targets):
        record = dict.fromkeys(FIELDS, "")
        record.update(variant_id=target["variant_id"], model=target["product_title"],
                      storage=target["storage"], color=target["color"], condition=target["condition"],
                      store="BestBuy.ca", offer_selection="MAIN_DISPLAYED_OFFER",
                      collection_method="USER_BROWSER_CAPTURE", status="HOLD")
        records.append(record)
        try:
            options = groups[target["variant_id"]]
            if not options:
                raise ValueError("No matching browser capture found in the selected folder.")
            newest = max(item[0] for item in options)
            latest = [item for item in options if item[0] == newest]
            # Identical duplicate downloads are harmless; contradictory captures
            # with the same timestamp must not be arbitrarily chosen.
            if len({json.dumps(item[2]["snapshot"], sort_keys=True) for item in latest}) != 1:
                for item in latest:
                    item[3].update(status="HOLD", reason="Conflicting captures at the same timestamp.")
                raise ValueError("Conflicting captures at the same timestamp; take one fresh capture.")
            chosen = latest[0]
            for item in options:
                if item is not chosen:
                    item[3].update(status="SUPERSEDED", reason="A newer or identical capture was selected.")
            _, path, data, audit = chosen
            # Freshness, seller, identity, stock and price are all checked again.
            # Never fall back to an older/cheaper capture if the newest fails.
            save_snapshot(run, index, target, record, data["snapshot"], data["checked_at"])
            audit["status"] = "PREVIEW_ONLY"
        except Exception as error:
            record["reason"] = str(error)
            for item in groups[target["variant_id"]]:
                if item[3]["status"] == "MATCHED":
                    item[3].update(status="HOLD", reason=str(error))
            print(f"HOLD: {target['storage']} {target['color']} {target['condition']}: {error}")
    write_report(run / "bestbuy_report.csv", records)
    (run / "capture_inputs.json").write_text(json.dumps(inputs, indent=2), encoding="utf-8")
    print("\nBatch report:")
    for row in records:
        print(f"{row['storage']} {row['color']} {row['condition']}: {row['status']} "
              f"{('$' + row['proposed_price_cad'] + ' CAD') if row['proposed_price_cad'] else row['reason']}")
    print("Report:", run / "bestbuy_report.csv")
    print("Preview only. No browser opened, no Shopify access or price changes.")
    return 0 if all(r["status"] == "PREVIEW_ONLY" for r in records) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--condition", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--capture-dir", type=Path, default=Path("."),
                        help="Folder containing bestbuy*.json captures; relative paths start at the project folder.")
    parser.add_argument("--catalogue", type=Path, default=Path("competitor_searches.csv"))
    args = parser.parse_args(argv)
    if args.start < 0 or not 1 <= args.limit <= 5:
        parser.error("Use --start >= 0 and --limit between 1 and 5.")
    folder = Path(__file__).resolve().parent
    capture_dir = args.capture_dir if args.capture_dir.is_absolute() else folder / args.capture_dir
    catalog_path = args.catalogue if args.catalogue.is_absolute() else folder / args.catalogue
    if not capture_dir.is_dir() or not catalog_path.is_file():
        parser.error("Capture folder or catalogue CSV does not exist.")
    try:
        targets = catalogue(catalog_path, args.model, args.condition)[args.start:args.start + args.limit]
    except ValueError as error:
        parser.error(str(error))
    if not targets:
        parser.error("No targets for that model and exact condition.")
    for target in targets:
        print(f"{target['product_title']} {target['storage']} {target['color']} {target['condition']} | {target['variant_id']}")
    run = folder / "bestbuy_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run.mkdir(parents=True, exist_ok=False)
    return batch(capture_dir, run, targets)


if __name__ == "__main__":
    raise SystemExit(main())
