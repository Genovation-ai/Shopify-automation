"""Offline regression tests for cross-colour proposal selection and provenance."""
import ast
import json
import re
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent_support as support


def target(identity, color, storage="128GB", model="iPhone 13", condition="Excellent"):
    return dict(variant_id=f"gid://shopify/ProductVariant/{identity}", product_title=model,
                storage=storage, color=color, condition=condition, competitor_source="Amazon.ca")


def offer(t, price, status="VERIFIED", age=0):
    return dict(checked_at=(datetime.now().astimezone() - timedelta(minutes=age)).isoformat(),
                shopify_variant_id=t["variant_id"], model=t["product_title"], storage=t["storage"],
                color=t["color"], condition=t["condition"], status=status, store="Amazon.ca",
                product_title=f"{t['product_title']} {t['storage']} {t['color']} Unlocked (Renewed)",
                price_cad=str(price), seller="Test Seller", source_url="https://www.amazon.ca/dp/B08PMGDBR4",
                reason="" if status == "VERIFIED" else "Not Excellent")


class ColourFallbackTests(unittest.TestCase):
    def prepare(self, targets, offers, wrong_source=False, different_product=False, with_scope=True):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copy2(ROOT / "batch_proposals.py", folder / "batch_proposals.py")
            if with_scope:
                support.write_rows(folder / "batch_targets.csv", targets, list(targets[0]))
            support.write_rows(folder / "amazon_verified_offers.csv", offers, list(offers[0]))
            lookup = {t["variant_id"]: t for t in targets}
            def post(url, **kwargs):
                identity = kwargs["json"]["variables"]["id"]
                t = lookup[identity]
                color = "Wrong" if wrong_source and identity == offers[0]["shopify_variant_id"] else t["color"]
                product = "gid://shopify/Product/2" if different_product and identity == offers[0]["shopify_variant_id"] else "gid://shopify/Product/1"
                variant = dict(id=identity, price="500.00",
                    product=dict(id=product, title=t["product_title"], status="ACTIVE"),
                    selectedOptions=[dict(name="Size", value=t["storage"]), dict(name="Color", value=color),
                                     dict(name="Condition", value=t["condition"])])
                return types.SimpleNamespace(status_code=200, json=lambda: {"data": {"productVariant": variant}})
            requests = types.ModuleType("requests")
            requests.post = post
            requests.RequestException = type("FakeRequestError", (Exception,), {})
            shopify = types.ModuleType("shopify_check")
            shopify.domain = "shop-techgenius.myshopify.com"
            shopify.token = "FAKE"
            shopify.data = {"shop": {"currencyCode": "CAD"}}
            error = None
            with patch.dict(sys.modules, {"requests": requests, "shopify_check": shopify}):
                try:
                    runpy.run_path(str(folder / "batch_proposals.py"), run_name="fallback_test")
                except SystemExit as ex:
                    error = str(ex.code)
            return support.read_rows(folder / "batch_price_proposals.csv"), error

    def test_exact_offer_takes_priority_over_cheaper_other_colour(self):
        black, white = target(1, "Black"), target(2, "White")
        rows, error = self.prepare([black, white], [offer(black, 600), offer(white, 550)])
        self.assertIsNone(error)
        self.assertEqual([r["proposed_price_cad"] for r in rows], ["597.00", "547.00"])
        self.assertTrue(all(r["pricing_basis"] == "EXACT_COLOUR" for r in rows))

    def test_missing_candidate_uses_lowest_peer_and_keeps_target_identity(self):
        black, white, pink = target(1, "Black"), target(2, "White"), target(3, "Pink")
        rows, error = self.prepare([black, white, pink], [offer(black, 600), offer(white, 550)])
        self.assertIsNone(error)
        fallback = rows[2]
        self.assertEqual(fallback["variant_id"], pink["variant_id"])
        self.assertEqual(fallback["color"], "Pink")
        self.assertEqual(fallback["pricing_basis"], "COLOUR_FALLBACK")
        self.assertEqual(fallback["source_color"], "White")
        self.assertEqual(fallback["source_variant_id"], white["variant_id"])
        self.assertEqual(fallback["proposed_price_cad"], "547.00")

    def test_held_exact_offer_can_use_verified_other_colour(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550), offer(pink, "", "HOLD")])
        self.assertIsNone(error)
        self.assertEqual(rows[1]["pricing_basis"], "COLOUR_FALLBACK")

    def test_cannot_cross_storage_or_model(self):
        black = target(1, "Black")
        wrong_storage = target(2, "Pink", storage="256GB")
        wrong_model = target(3, "Pink", model="iPhone 13 Pro")
        rows, error = self.prepare([black, wrong_storage, wrong_model], [offer(black, 550)])
        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)

    def test_nonexcellent_source_cannot_supply_fallback(self):
        good, pink = target(1, "Black", condition="Good"), target(3, "Pink")
        rows, error = self.prepare([good, pink], [offer(good, 550)])
        self.assertEqual(rows, [])
        self.assertIn("Unexpected verified source", error)

    def test_stale_lowest_source_is_excluded(self):
        black, white, pink = target(1, "Black"), target(2, "White"), target(3, "Pink")
        rows, error = self.prepare([black, white, pink], [offer(black, 400, age=16), offer(white, 550)])
        self.assertIsNone(error)
        self.assertEqual(rows[2]["proposed_price_cad"], "547.00")
        self.assertEqual(rows[2]["source_color"], "White")

    def test_no_fresh_source_cannot_create_proposal(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550, age=16)])
        self.assertEqual(rows, [])
        self.assertIn("No fresh", error)

    def test_source_shopify_identity_is_checked(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550)], wrong_source=True)
        self.assertEqual(rows, [])
        self.assertIn("identity does not match", error)

    def test_different_shopify_product_cannot_be_peer(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550)], different_product=True)
        self.assertEqual(rows, [])
        self.assertIn("different Shopify product", error)

    def test_standalone_without_target_scope_stays_exact_only(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550), offer(pink, "", "HOLD")], with_scope=False)
        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)

    def test_fallback_is_visible_even_with_no_target_candidate(self):
        black, pink = target(1, "Black"), target(3, "Pink")
        rows, error = self.prepare([black, pink], [offer(black, 550)])
        report = support.report_rows([pink], [], [], rows, [], "PREVIEW")
        self.assertEqual(report[0]["status"], "PREVIEW")
        self.assertEqual(report[0]["pricing_basis"], "COLOUR_FALLBACK")
        self.assertIn("Black", report[0]["reason"])

    def test_fallback_cannot_turn_into_verified_source(self):
        pink = target(3, "Pink")
        fake = offer(target(1, "Black"), 550, status="COLOUR_FALLBACK")
        self.assertIsNone(support.choose_offer(pink, [fake]))


if __name__ == "__main__":
    unittest.main()
