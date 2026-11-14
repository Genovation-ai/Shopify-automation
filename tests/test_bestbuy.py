import copy
import unittest
import csv
import tempfile
import json
import subprocess
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

from bestbuy_support import canonical_url, price_from_offer, verify_snapshot
import bestbuy_preview

URL = "https://www.bestbuy.ca/en-ca/product/refurbished-excellent-iphone-14/16553733"
TARGET = {"product_title": "iPhone 14", "storage": "128GB", "color": "Red", "condition": "Good"}
SNAPSHOT = {
    "url": URL, "title": "Refurbished - Good Apple iPhone 14 128GB Red Unlocked",
    "sku_text": "Web Code: 16553733", "seller_text": "Sold and shipped by Example Seller | 4.0 seller rating",
    "offer_text": "$549.99", "availability_text": "Add to Cart", "cart_enabled": True,
    "evidence_text": "Nano-SIM + eSIM",
    "specs_text": "Product Condition\nRefurbished Good\nWireless Carrier\nUnlocked\nPhone Model\niPhone 14\nStorage\n128GB\nSIM Card\nNano SIM + eSIM",
}


class BestBuyTests(unittest.TestCase):
    def test_valid_primary_offer(self):
        self.assertEqual(verify_snapshot(SNAPSHOT, TARGET, URL), (Decimal("549.99"), "Example Seller"))

    def test_identical_display_price_is_not_second_offer(self):
        self.assertEqual(price_from_offer("$549.99\n$549\n.\n99"), Decimal("549.99"))

    def test_shipping_price_is_not_item_price(self):
        with self.assertRaises(ValueError):
            price_from_offer("$549.99\n$5.54 delivery")

    def test_second_unlabelled_price_blocks(self):
        with self.assertRaises(ValueError):
            price_from_offer("$549.99\n$599.99")

    def test_labelled_savings_are_not_price(self):
        self.assertEqual(price_from_offer("$549.99\nSAVE $50"), Decimal("549.99"))

    def test_financing_and_usd_block(self):
        for text in ("US$549.99", "$25.00/mo", "From $549.99"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                price_from_offer(text)

    def rejected(self, field, value):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot[field] = value
        with self.assertRaises(ValueError):
            verify_snapshot(snapshot, TARGET, URL)

    def test_redirect_to_other_product_blocks(self):
        self.rejected("url", URL.replace("16553733", "16553734"))

    def test_visible_sku_mismatch_blocks(self):
        self.rejected("sku_text", "Web Code: 16553734")

    def test_title_model_storage_colour_grade_mismatches_block(self):
        for old, new in (("iPhone 14", "iPhone 14 Plus"), ("128GB", "256GB"),
                         ("Red", "Blue"), ("Good", "Excellent"), ("Good", "Very Good")):
            with self.subTest(new=new):
                self.rejected("title", SNAPSHOT["title"].replace(old, new))

    def test_locked_evidence_blocks_unlocked_title(self):
        self.rejected("evidence_text", "This device is locked to Verizon only.")

    def test_nano_only_is_not_dual_sim_confirmation(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot["evidence_text"] = "Nano SIM"
        snapshot["specs_text"] = snapshot["specs_text"].replace("Nano SIM + eSIM", "Nano SIM")
        with self.assertRaises(ValueError):
            verify_snapshot(snapshot, TARGET, URL)

    def test_esim_only_blocks(self):
        self.rejected("evidence_text", "This is an eSIM only model.")

    def test_conflicting_spec_model_and_storage_block(self):
        for old, new in (("iPhone 14", "iPhone 14 Plus"), ("128GB", "256GB")):
            self.rejected("specs_text", SNAPSHOT["specs_text"].replace(old, new))

    def test_disabled_cart_and_negative_stock_block(self):
        self.rejected("cart_enabled", False)
        self.rejected("availability_text", "Sold out")

    def test_missing_seller_blocks(self):
        self.rejected("seller_text", "Customers also bought Seller Example")

    def test_wrong_host_and_nonproduct_url_block(self):
        for url in ("https://bestbuy.ca.evil.example/en-ca/product/phone/16553733",
                    "https://www.bestbuy.ca/en-ca/search?search=iphone", URL.replace("https", "http")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_url(url)

    def test_apply_flag_is_not_supported(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as error:
            bestbuy_preview.main(["--model", "iPhone 14", "--apply"])
        self.assertEqual(error.exception.code, 2)

    def test_offline_list_does_not_import_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_catalogue(folder)
            self.assertEqual(bestbuy_preview.main(["--model", "iPhone 14", "--condition", "Good",
                                                  "--catalogue", str(path), "--list-targets"]), 0)

    def write_catalogue(self, folder):
        path = Path(folder) / "targets.csv"
        rows = [dict(TARGET, condition=grade, variant_id=f"gid://shopify/ProductVariant/{i}")
                for i, grade in enumerate(("Excellent", "Good", "Very Good"), 1)]
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_condition_selection_keeps_distinct_variant_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_catalogue(folder)
            self.assertEqual(bestbuy_preview.catalogue(path, "iPhone 14", "Good")[0]["variant_id"],
                             "gid://shopify/ProductVariant/2")
            self.assertEqual(bestbuy_preview.catalogue(path, "iPhone 14", "Very Good")[0]["variant_id"],
                             "gid://shopify/ProductVariant/3")

    def test_excellent_targets_cannot_enter_bestbuy(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                bestbuy_preview.catalogue(self.write_catalogue(folder), "iPhone 14", "Excellent")
        with self.assertRaises(ValueError):
            verify_snapshot(SNAPSHOT, dict(TARGET, condition="Excellent"), URL)

    def test_very_good_is_not_good(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot["title"] = snapshot["title"].replace("Good", "Very Good")
        snapshot["specs_text"] = snapshot["specs_text"].replace("Good", "Very Good")
        self.assertEqual(verify_snapshot(snapshot, dict(TARGET, condition="Very Good"), URL)[0], Decimal("549.99"))
        with self.assertRaises(ValueError):
            verify_snapshot(snapshot, TARGET, URL)

    def test_conflicting_condition_specs_block(self):
        self.rejected("specs_text", SNAPSHOT["specs_text"].replace("Refurbished Good", "Refurbished Excellent"))

    def test_access_denied_stays_on_hold(self):
        from unittest.mock import Mock
        page = Mock()
        page.title.return_value = "Access Denied"
        with self.assertRaisesRegex(ValueError, "denied browser access"):
            bestbuy_preview.ensure_access(page)

    def test_main_offer_excludes_labelled_outlet_section(self):
        text = ("SAVE $220\n$379.88\nSale ends: January 31, 2027\nShop Outlet Deals\n"
                "Open Box from $458.96\nRefurbished from $334.99\nBrand New at $870.00")
        self.assertEqual(price_from_offer(text), Decimal("379.88"))

    def test_outlet_price_never_substitutes_for_missing_main_price(self):
        with self.assertRaises(ValueError):
            price_from_offer("Sale ends tomorrow\nShop Outlet Deals\nRefurbished from $334.99")

    def test_primary_ambiguity_not_hidden_by_outlet_boundary(self):
        with self.assertRaises(ValueError):
            price_from_offer("$379.88\n$399.99\nShop Outlet Deals\nRefurbished from $334.99")

    def test_unlabelled_from_price_remains_ambiguous(self):
        with self.assertRaises(ValueError):
            price_from_offer("$379.88\nRefurbished from $334.99")

    def test_storage_spacing_is_not_mismatch(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot["specs_text"] = snapshot["specs_text"].replace("128GB", "128 GB")
        self.assertEqual(verify_snapshot(snapshot, TARGET, URL)[0], Decimal("549.99"))

    def test_import_saves_main_offer_and_provenance_without_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.write_catalogue(folder)
            snapshot = copy.deepcopy(SNAPSHOT)
            snapshot["offer_text"] = "$379.88\nShop Outlet Deals\nRefurbished from $334.99"
            capture = path / "capture.json"
            capture.write_text(json.dumps({"schema_version": 1, "source": "BESTBUY_VISIBLE_MAIN_OFFER",
                                          "checked_at": datetime.now(timezone.utc).isoformat(), "snapshot": snapshot}))
            with patch.object(bestbuy_preview, "__file__", str(path / "bestbuy_preview.py")), \
                 patch.dict(sys.modules, {"playwright.sync_api": None}):
                result = bestbuy_preview.main(["--model", "iPhone 14", "--condition", "Good",
                                               "--catalogue", str(path / "targets.csv"),
                                               "--capture-file", str(capture)])
            self.assertEqual(result, 0)
            report = list((path / "bestbuy_runs").glob("*/bestbuy_report.csv"))[0]
            with report.open(encoding="utf-8-sig", newline="") as file:
                row = list(csv.DictReader(file))[0]
            self.assertEqual(row["variant_id"], "gid://shopify/ProductVariant/2")
            self.assertEqual(row["proposed_price_cad"], "376.88")
            self.assertEqual(row["offer_selection"], "MAIN_DISPLAYED_OFFER")
            self.assertEqual(row["seller"], "Example Seller")
            self.assertEqual(row["status"], "PREVIEW_ONLY")
            self.assertTrue(list(report.parent.glob("evidence_*.json")))

    def test_old_capture_cannot_be_made_fresh_by_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            capture = path / "capture.json"
            capture.write_text(json.dumps({"schema_version": 1, "source": "BESTBUY_VISIBLE_MAIN_OFFER",
                "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
                "snapshot": SNAPSHOT}))
            target = dict(TARGET, variant_id="gid://shopify/ProductVariant/2")
            self.assertEqual(bestbuy_preview.import_capture(capture, path, target), 1)
            with (path / "bestbuy_report.csv").open(encoding="utf-8-sig", newline="") as file:
                row = list(csv.DictReader(file))[0]
            self.assertEqual(row["status"], "HOLD")
            self.assertEqual(row["proposed_price_cad"], "")

    @unittest.skipUnless(shutil.which("node"), "Node optional: Python validation tests still run")
    def test_bookmark_capture_only_downloads_visible_primary_product(self):
        fixture = Path(__file__).with_name("browser_capture_fixture.cjs")
        script = Path(bestbuy_preview.__file__).with_name("bestbuy_capture.js")
        for mode in ("normal", "collapsed", "wrong-host", "ambiguous", "disabled-cart", "ambiguous-cart", "loading-price"):
            with self.subTest(mode=mode):
                result = subprocess.run([shutil.which("node"), str(fixture), str(script), mode],
                                        capture_output=True, text=True, check=True)
                data = json.loads(result.stdout)
                if mode in {"normal", "disabled-cart"}:
                    self.assertEqual(data["clicks"], ["download"])
                    capture = json.loads(data["file"])
                    self.assertEqual(capture["snapshot"]["cart_enabled"], mode == "normal")
                    self.assertEqual(capture["snapshot"]["seller_text"], SNAPSHOT["seller_text"])
                    self.assertEqual(capture["snapshot"]["offer_text"], "SAVE $220\n$379.88")
                    self.assertNotIn("Compare more", capture["snapshot"]["availability_text"])
                    self.assertEqual(capture["collector_version"], 2)
                else:
                    self.assertEqual(data["clicks"], [])
                    self.assertIsNone(data["file"])
                    self.assertIn("CAPTURE HOLD", data["alerts"][0])

    def test_uploaded_page_structure_scopes_main_offer_not_sticky_cart(self):
        try:
            from lxml import html
        except ImportError:
            self.skipTest("Optional lxml for saved-page structural regression")
        tree = html.parse(str(Path(__file__).with_name("browser_capture_layout.html")))
        summary = tree.xpath("//*[@data-automation='layout-product-summary']")
        offer = tree.xpath("//*[@data-automation='layout-product-offer-details']")
        self.assertEqual(len(summary), 1)
        self.assertEqual(len(offer), 1)
        self.assertEqual(len(summary[0].xpath(".//h1")), 1)
        self.assertEqual(len(summary[0].xpath(".//*[@data-automation='SKU_ID']")), 1)
        self.assertEqual(len(offer[0].xpath(".//*[contains(@class,'pricingContainer_')]")), 1)
        delivery = offer[0].xpath(".//*[@data-automation='delivery-tab']")
        self.assertEqual(len(delivery), 1)
        self.assertEqual(len(delivery[0].xpath(".//*[@data-testid='shipping-container']")), 1)
        carts = delivery[0].xpath(".//button[@data-automation='add-to-cart-button' and "
                                 "not(ancestor::section[contains(concat(' ',normalize-space(@class),' '),' modal ')])]")
        self.assertEqual(len(carts), 1)
        self.assertTrue(tree.xpath("//*[@data-automation='layout-product-state-actions']//button[@data-automation='add-to-cart-button']"))
        self.assertFalse(tree.xpath("//*[@data-automation='layout-product-header']//h1"))
        self.assertFalse(tree.xpath("//*[@data-testid='SKU_ID']"))


if __name__ == "__main__":
    unittest.main()
