import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bestbuy_batch_preview as batch


class BestBuyBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.run = self.folder / "run"
        self.run.mkdir()
        self.now = datetime.now(timezone.utc)
        self.targets = [dict(product_title="iPhone 13", storage="128GB", color=color,
                             condition="Good", variant_id=f"gid://shopify/ProductVariant/{i}")
                        for i, color in enumerate(("Blue", "Pink", "Midnight"), 1)]

    def capture(self, name, color="Blue", price="378.00", timestamp=None, condition="Good"):
        data = {"schema_version": 1, "source": "BESTBUY_VISIBLE_MAIN_OFFER",
                "checked_at": (timestamp or self.now).isoformat(), "snapshot": {
            "url": "https://www.bestbuy.ca/en-ca/product/refurbished-phone/15922548",
            "title": f"Refurbished ({condition}) - Apple iPhone 13 128GB - {color} - Unlocked",
            "sku_text": "Web Code: 15922548", "seller_text": "Sold and shipped by Test Seller | 4 stars",
            "offer_text": "$" + price, "availability_text": "Available online only",
            "cart_enabled": True, "evidence_text": f"Refurbished {condition}",
            "specs_text": f"Product Condition\nRefurbished {condition}\nWireless Carrier\nUnlocked\nStorage\n128 GB\nPhone Model\niPhone 13"}}
        path = self.folder / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def rows(self):
        with (self.run / "bestbuy_report.csv").open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def test_one_report_routes_by_contents_and_accepts_double_extension(self):
        self.capture("bestbuy_midnight.json.json", "Pink", "459.96")
        self.capture("bestbuy_pink.json", "Midnight", "382.00")
        self.capture("bestbuy_capture (1).json")
        self.assertEqual(batch.batch(self.folder, self.run, self.targets), 0)
        rows = self.rows()
        self.assertEqual([r["proposed_price_cad"] for r in rows], ["375.00", "456.96", "379.00"])
        self.assertEqual([r["variant_id"] for r in rows], [t["variant_id"] for t in self.targets])
        self.assertTrue(all(r["seller"] == "Test Seller" and r["source_url"] for r in rows))
        self.assertEqual(len(list(self.run.glob("evidence_*.json"))), 3)

    def test_stale_and_missing_targets_reported_without_losing_fresh_row(self):
        self.capture("bestbuy_blue.json", timestamp=self.now - timedelta(minutes=16))
        self.capture("bestbuy_pink.json", "Pink")
        self.assertEqual(batch.batch(self.folder, self.run, self.targets), 1)
        self.assertEqual([r["status"] for r in self.rows()], ["HOLD", "PREVIEW_ONLY", "HOLD"])
        self.assertEqual(self.rows()[0]["proposed_price_cad"], "")

    def test_latest_offer_wins_even_when_more_expensive(self):
        self.capture("bestbuy_old.json", price="200.00", timestamp=self.now - timedelta(minutes=1))
        self.capture("bestbuy_new.json", price="300.00")
        self.assertEqual(batch.batch(self.folder, self.run, self.targets[:1]), 0)
        self.assertEqual(self.rows()[0]["proposed_price_cad"], "297.00")

    def test_newer_unavailable_offer_does_not_reuse_older_price(self):
        self.capture("bestbuy_old.json", timestamp=self.now - timedelta(minutes=1))
        path = self.capture("bestbuy_new.json")
        data = json.loads(path.read_text())
        data["snapshot"]["cart_enabled"] = False
        path.write_text(json.dumps(data))
        self.assertEqual(batch.batch(self.folder, self.run, self.targets[:1]), 1)
        self.assertEqual(self.rows()[0]["proposed_price_cad"], "")

    def test_conflicting_same_time_captures_hold_instead_of_choosing(self):
        self.capture("bestbuy_first.json", price="200.00")
        self.capture("bestbuy_second.json", price="300.00")
        self.assertEqual(batch.batch(self.folder, self.run, self.targets[:1]), 1)
        self.assertIn("Conflicting captures", self.rows()[0]["reason"])

    def test_good_never_substitutes_for_very_good(self):
        self.capture("bestbuy_blue.json")
        target = dict(self.targets[0], condition="Very Good")
        self.assertEqual(batch.batch(self.folder, self.run, [target]), 1)
        self.assertEqual(self.rows()[0]["proposed_price_cad"], "")

    def test_old_evidence_and_malformed_capture_are_audited_not_used(self):
        (self.folder / "bestbuy_evidence.json").write_text(json.dumps({"checked_at": self.now.isoformat(), "snapshot": {}}))
        (self.folder / "bestbuy_broken.json").write_text("{")
        self.assertEqual(batch.batch(self.folder, self.run, self.targets[:1]), 1)
        audit = json.loads((self.run / "capture_inputs.json").read_text())
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(row["status"] == "HOLD" for row in audit))

    def test_command_is_offline_and_resolves_paths_from_project_folder(self):
        self.capture("bestbuy_capture.json")
        path = self.folder / "competitor_searches.csv"
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=self.targets[0].keys())
            writer.writeheader()
            writer.writerows(self.targets)
        with patch.object(batch, "__file__", str(self.folder / "bestbuy_batch_preview.py")), \
             patch.dict(sys.modules, {"playwright.sync_api": None}):
            self.assertEqual(batch.main(["--model", "iPhone 13", "--condition", "Good", "--limit", "1"]), 0)
        self.assertEqual(len(list((self.folder / "bestbuy_runs").glob("*/bestbuy_report.csv"))), 1)

    def test_apply_flag_is_not_available(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as error:
            batch.main(["--model", "iPhone 13", "--condition", "Good", "--apply"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
