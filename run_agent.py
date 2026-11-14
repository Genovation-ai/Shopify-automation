"""Supervised Amazon pricing runs. Preview by default; --apply explicitly writes prices.

--all processes ONE model by storage group, searching in small batches.
--approve-targets approves the displayed selection only; it makes no network calls.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agent_support import (REPORT_FIELDS, approve_targets, candidate_key, catalogue,
                           fresh, offer_key, read_rows, report_rows, target_key, write_rows)

OUTPUTS = ("amazon_candidates.csv", "amazon_verified_offers.csv",
           "batch_price_proposals.csv", "batch_update_results.csv", "batch_targets.csv")


def run_chunk(folder, args, targets, start, archive, indices=None):
    started = datetime.now().astimezone()
    archive.mkdir(parents=True)
    for name in OUTPUTS:
        path = folder / name
        if path.exists():
            path.replace(archive / ("previous_" + name))
    state, reason = "PREVIEW", ""
    try:
        write_rows(folder / "batch_targets.csv", targets, list(targets[0]))
        for script in ("amazon_batch.py", "amazon_verify.py", "batch_update.py"):
            if script == "amazon_batch.py":
                # A storage group may span search batches. Keep all its source
                # colours together for verification and lowest-peer selection.
                jobs = []
                search_indices = indices if indices is not None else list(range(start, start + len(targets)))
                for index in search_indices:
                    if jobs and index == jobs[-1][-1] + 1 and len(jobs[-1]) < args.limit:
                        jobs[-1].append(index)
                    else:
                        jobs.append([index])
                candidates = []
                fields = ["checked_at"]
                allowed = {target_key(t) for t in targets}
                for job in jobs:
                    command = [sys.executable, "-u", str(folder / script), "--model", args.model,
                               "--start", str(job[0]), "--limit", str(len(job)), "--auto-close"]
                    print("\nRUNNING: " + script, flush=True)
                    result = subprocess.run(command, cwd=folder)
                    if result.returncode:
                        raise ValueError(f"{script} failed (exit {result.returncode}); see terminal output.")
                    for c in read_rows(folder / OUTPUTS[0]):
                        fresh(c["checked_at"], started)
                        if candidate_key(c) not in allowed:
                            raise ValueError("Candidate is outside the selected storage group.")
                        fields = list(c)
                        candidates.append(c)
                write_rows(folder / OUTPUTS[0], candidates, fields)
                continue
            if script == "amazon_verify.py" and not read_rows(folder / OUTPUTS[0]):
                state = "NO_CANDIDATE"
                break
            if script == "batch_update.py" and not any(
                o["status"] == "VERIFIED" for o in read_rows(folder / OUTPUTS[1])
            ):
                state = "HOLD"
                break
            command = [sys.executable, "-u", str(folder / script)]
            if script == "amazon_verify.py":
                command += ["--auto-close"]
            elif args.apply:
                command += ["--apply"]
            print("\nRUNNING: " + script, flush=True)
            result = subprocess.run(command, cwd=folder)
            if result.returncode:
                raise ValueError(f"{script} failed (exit {result.returncode}); see terminal output.")
            if script == "amazon_verify.py":
                allowed = {(candidate_key(c), c["candidate_url"])
                           for c in read_rows(folder / OUTPUTS[0])}
                for o in read_rows(folder / OUTPUTS[1]):
                    fresh(o["checked_at"], started)
                    if (offer_key(o), o["source_url"]) not in allowed:
                        raise ValueError("Offer is outside the current search batch.")
        if args.apply:
            state = "HOLD"  # Only updater read-back results may claim APPLIED_AND_VERIFIED.
    except KeyboardInterrupt:
        state, reason = "INTERRUPTED", "Run interrupted. Check audit log before retrying any writes."
        raise
    except (ValueError, OSError, KeyError, TypeError) as error:
        state, reason = "ERROR", str(error)
        raise
    finally:
        records = report_rows(targets, *(read_rows(folder / n) for n in OUTPUTS[:4]), state, reason)
        write_rows(archive / "report.csv", records, REPORT_FIELDS)
        for name in OUTPUTS:
            if (folder / name).exists():
                shutil.copy2(folder / name, archive / name)
        # Persist this chunk even if it failed or was interrupted.
        all_records = read_rows(folder / "run_report.csv") + records
        write_rows(folder / "run_report.csv", all_records, REPORT_FIELDS)
        print("\nBatch report:", flush=True)
        for row in records:
            print(f"{row['storage']} {row['color']}: {row['status']} {row['reason']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=3, help="Targets per search batch, 1 to 5.")
    parser.add_argument("--all", action="store_true", help="Process this model from --start to its end.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list-targets", action="store_true")
    modes.add_argument("--approve-targets", action="store_true",
                       help="Explicitly approve selected target identities locally; no price changes.")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.start < 0 or not 1 <= args.limit <= 5:
        parser.error("--start must be nonnegative; --limit must be between 1 and 5.")
    folder = Path(__file__).resolve().parent
    matching = catalogue(folder, args.model)
    selected = matching[args.start:] if args.all else matching[args.start:args.start + args.limit]
    if not selected:
        raise ValueError(f"No targets for {args.model} at start {args.start}.")
    print(f"Selected {len(selected)} of {len(matching)} entries for {args.model}; skipping {args.start}.")
    for i, t in enumerate(selected, args.start):
        print(f"[{i}] {t['product_title']} {t['storage']} {t['color']} | {t['variant_id']}")
    if args.list_targets:
        return
    missing = [n for n in ("batch_update.py",) if not (folder / n).exists()]
    if missing:
        raise ValueError("Missing files: " + ", ".join(missing))
    lock = folder / "price_agent.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("price_agent.lock exists. Confirm the previous run stopped before removing it.")
    os.close(descriptor)
    try:
        if args.approve_targets:
            approve_targets(folder, selected)
            print(f"Approved {len(selected)} target identities in approved_targets.json. No prices changed.")
            return
        required = ("amazon_batch.py", "amazon_verify.py", "batch_proposals.py", "shopify_check.py")
        missing = [n for n in required if not (folder / n).is_file()]
        if missing:
            raise ValueError("Missing files: " + ", ".join(missing))
        root = folder / "agent_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        root.mkdir(parents=True)
        report = folder / "run_report.csv"
        if report.exists():
            report.replace(root / "previous_run_report.csv")
        try:
            if args.all:
                groups = {}
                for index, target in enumerate(selected, args.start):
                    key = (target["product_title"], target["storage"], target["condition"])
                    groups.setdefault(key, []).append((index, target))
                for group in groups.values():
                    indices = [i for i, t in group]
                    run_chunk(folder, args, [t for i, t in group], indices[0],
                              root / f"batch_{indices[0]:04d}", indices=indices)
            else:
                run_chunk(folder, args, selected, args.start, root / f"batch_{args.start:04d}")
        finally:
            completed = read_rows(report)
            completed_ids = {r["variant_id"] for r in completed}
            remaining = [t for t in selected if t["variant_id"] not in completed_ids]
            if remaining:
                pending = report_rows(remaining, [], [], [], [], "ERROR",
                                      "Earlier batch stopped; this target was not processed.")
                for row in pending:
                    row["status"] = "NOT_PROCESSED"
                write_rows(report, completed + pending, REPORT_FIELDS)
            if report.exists():
                shutil.copy2(report, root / "run_report.csv")
        print("\nRun complete. Review run_report.csv for every selected target.")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit("HOLD: " + str(error))
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Review run_report.csv and price_update_log.jsonl before retrying.")
