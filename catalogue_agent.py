"""Supervised catalogue runs with durable storage-group checkpoints.

Preview by default. Existing target approvals still govern every price write.
"""
import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import run_agent
from agent_support import (REPORT_FIELDS, catalogue, read_rows, report_rows,
                           replace_with_retry, write_rows)

CHECKPOINT = "catalogue_checkpoint.json"
REPORT = "catalogue_report.csv"


def make_plan(folder, models=None):
    rows = read_rows(folder / "competitor_searches.csv")
    available = list(dict.fromkeys(r["product_title"] for r in rows
                     if r["competitor_source"] == "Amazon.ca"
                     and r["condition"] == "Excellent"))
    selected = list(dict.fromkeys(models)) if models is not None else available
    if not selected or any(m not in available for m in selected):
        raise ValueError("No eligible catalogue entries, or an unknown model was selected.")
    plan, seen = [], set()
    for model in selected:
        groups = {}
        for index, target in enumerate(catalogue(folder, model)):
            if target["variant_id"] in seen:
                raise ValueError("Duplicate variant across catalogue models.")
            seen.add(target["variant_id"])
            groups.setdefault((target["storage"], target["condition"]), []).append((index, target))
        for group in groups.values():
            plan.append({"model": model, "indices": [i for i, _ in group],
                         "targets": [t for _, t in group]})
    return plan


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def save_checkpoint(folder, checkpoint):
    path = folder / CHECKPOINT
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    replace_with_retry(temporary, path)


def pending_rows(group, reason):
    rows = report_rows(group["targets"], [], [], [], [], "ERROR", reason)
    for row in rows:
        row["status"] = "NOT_PROCESSED"
    return rows


def combined_report(folder, checkpoint):
    rows = []
    for group in checkpoint["groups"]:
        rows.extend(group.get("records") or pending_rows(group, "Group has not completed."))
    write_rows(folder / REPORT, rows, REPORT_FIELDS)
    write_rows(Path(checkpoint["archive"]) / REPORT, rows, REPORT_FIELDS)
    return rows


def execute(folder, args):
    folder = Path(folder).resolve()
    path = folder / CHECKPOINT
    if args.resume:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint.get("schema_version") != 1:
            raise ValueError("Unsupported catalogue checkpoint.")
        if checkpoint["apply"] != args.apply:
            raise ValueError("Resume must use the original mode; add --apply only for an apply checkpoint.")
        if args.apply and any(g["state"] == "RUNNING" for g in checkpoint["groups"]):
            raise ValueError("Apply ended without a checkpoint. Check Shopify and the audit log, then start a new preview with --new-run.")
        plan = make_plan(folder, checkpoint["models"])
        if fingerprint(plan) != checkpoint["fingerprint"]:
            raise ValueError("Catalogue changed since this run. Start a new preview with --new-run.")
        if args.apply and any(r["status"] == "UPDATE_UNCONFIRMED"
                              for g in checkpoint["groups"] for r in g.get("records", [])):
            raise ValueError("Unconfirmed update: check Shopify and the audit log, then start a new preview. Apply resume is blocked.")
    else:
        if path.exists() and not args.new_run:
            old = json.loads(path.read_text(encoding="utf-8"))
            if any(g["state"] != "DONE" for g in old["groups"]):
                raise ValueError("An unfinished catalogue run exists. Use --resume, or --new-run for a fresh run.")
        plan = make_plan(folder, args.models)
        archive = folder / "catalogue_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive.mkdir(parents=True)
        for name in (CHECKPOINT, REPORT):
            if (folder / name).exists():
                shutil.copy2(folder / name, archive / ("previous_" + name))
        checkpoint = {"schema_version": 1, "apply": args.apply,
                      "models": list(dict.fromkeys(g["model"] for g in plan)),
                      "fingerprint": fingerprint(plan), "archive": str(archive),
                      "groups": [dict(g, state="PENDING", attempts=0, records=[]) for g in plan]}
        save_checkpoint(folder, checkpoint)
    combined_report(folder, checkpoint)
    for number, group in enumerate(checkpoint["groups"]):
        if group["state"] == "DONE":
            continue
        group["attempts"] += 1
        group["state"] = "RUNNING"
        save_checkpoint(folder, checkpoint)  # Must succeed before any external work.
        archive = Path(checkpoint["archive"]) / f"group_{number:04d}_attempt_{group['attempts']:03d}"
        # run_chunk owns this working report; the combined report is separate.
        if (folder / "run_report.csv").exists():
            backup = Path(checkpoint["archive"]) / f"before_group_{number:04d}_{group['attempts']:03d}.csv"
            replace_with_retry(folder / "run_report.csv", backup)
        error = None
        try:
            print(f"\nCATALOGUE GROUP {number + 1}/{len(checkpoint['groups'])}: "
                  f"{group['model']} {group['targets'][0]['storage']}", flush=True)
            run_agent.run_chunk(folder, SimpleNamespace(model=group["model"], apply=args.apply,
                                limit=args.limit), group["targets"], group["indices"][0],
                                archive, indices=group["indices"])
            group["state"] = "DONE"
        except (Exception, KeyboardInterrupt) as exc:
            group["state"] = "FAILED"
            error = exc
        finally:
            records = read_rows(archive / "report.csv")
            if records and {r["variant_id"] for r in records} != {t["variant_id"] for t in group["targets"]}:
                records = []
                error = ValueError("Group report does not match selected identities.")
                group["state"] = "FAILED"
            if not records:
                records = pending_rows(group, str(error or "Missing group report."))
                group["state"] = "FAILED"
                error = error or ValueError("Missing group report.")
            group["records"] = records
            save_checkpoint(folder, checkpoint)
            combined_report(folder, checkpoint)
        if error is not None:
            raise error
    print("\nCatalogue run complete. Review catalogue_report.csv; HOLD and NO_CANDIDATE rows remain unresolved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all-models", action="store_true")
    selection.add_argument("--models", nargs="+", help='Exact names, e.g. "iPhone 11" "iPhone 12".')
    selection.add_argument("--resume", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply only previously approved target identities.")
    parser.add_argument("--list-targets", action="store_true", help="Offline selection only.")
    parser.add_argument("--new-run", action="store_true", help="Archive the checkpoint and start fresh.")
    parser.add_argument("--limit", type=int, default=3, help="Search batch size, 1 to 5.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 5 or (args.resume and (args.new_run or args.list_targets)):
        parser.error("Limit must be 1 to 5; --resume cannot use --new-run or --list-targets.")
    folder = Path(__file__).resolve().parent
    if args.list_targets:
        plan = make_plan(folder, args.models)
        for g in plan:
            print(f"{g['model']} {g['targets'][0]['storage']}: {len(g['targets'])} targets")
        print(f"Total: {sum(len(g['targets']) for g in plan)} targets in {len(plan)} groups. No network access or price changes.")
        return
    for name in ("run_agent.py", "agent_support.py", "amazon_batch.py", "amazon_verify.py",
                 "batch_update.py", "batch_proposals.py", "shopify_check.py", "competitor_searches.csv"):
        if not (folder / name).is_file():
            raise ValueError("Missing required file: " + name)
    lock = folder / "price_agent.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("Another run or stale price_agent.lock exists. Confirm the previous process stopped.")
    os.close(descriptor)
    try:
        execute(folder, args)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit("HOLD: " + str(exc))
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Review catalogue_report.csv and price_update_log.jsonl before resuming.")
