"""Offline checks; Shopify and browser modules are replaced with local doubles."""
import ast
import csv
import importlib.util
import json
import runpy
import shutil
import sys
import tempfile
import types
import unittest
import re
from decimal import Decimal
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent_support as support
import run_agent as runner


def proposal(variant="gid://shopify/ProductVariant/41648358686767", color="Gold", price="706.00"):
    return dict(variant_id=variant, model="iPhone 12 Pro Max", storage="512GB", color=color,
                condition="Excellent", product_id="gid://shopify/Product/7274586439727",
                offer_checked_at=datetime.now().astimezone().isoformat(),
                current_shopify_price_cad="626.00", proposed_price_cad=price,
                competitor_price_cad=str(float(price) + 3), action="INCREASE",
                source_url="https://www.amazon.ca/dp/B08PMGDBR4")


class WorkflowTests(unittest.TestCase):
    def test_explicit_approval_command_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copy2(ROOT / "run_agent.py", folder / "run_agent.py")
            shutil.copy2(ROOT / "batch_update.py", folder / "batch_update.py")
            target = dict(variant_id="gid://shopify/ProductVariant/999", product_title="iPhone 12 Pro",
                          storage="128GB", color="Gold", condition="Excellent", competitor_source="Amazon.ca")
            support.write_rows(folder / "competitor_searches.csv", [target], list(target))
            with patch.object(sys, "argv", ["run_agent.py", "--model", "iPhone 12 Pro", "--all", "--approve-targets"]), \
                 patch("subprocess.run", side_effect=AssertionError("Network scripts must not run")):
                runpy.run_path(str(folder / "run_agent.py"), run_name="__main__")
            approved = support.approval_records(folder)
            self.assertEqual(approved[0]["storage"], "128GB")
            self.assertFalse((folder / "price_agent.lock").exists())

    def test_empty_search_cannot_reuse_old_offers(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            old = dict(shopify_variant_id="old", status="VERIFIED")
            support.write_rows(folder / "amazon_verified_offers.csv", [old], list(old))
            target = dict(variant_id="id", product_title="iPhone 12 Pro", storage="512GB",
                          color="Gold", condition="Excellent")
            calls = []
            def search_only(command, **kwargs):
                calls.append(Path(command[2]).name)
                support.write_rows(folder / "amazon_candidates.csv", [], ["checked_at"])
                return types.SimpleNamespace(returncode=0)
            with patch("subprocess.run", side_effect=search_only):
                runner.run_chunk(folder, types.SimpleNamespace(model="iPhone 12 Pro", apply=False),
                                 [target], 0, folder / "archive")
            self.assertEqual(calls, ["amazon_batch.py"])
            self.assertEqual(support.read_rows(folder / "run_report.csv")[0]["status"], "NO_CANDIDATE")
            self.assertFalse((folder / "amazon_verified_offers.csv").exists())

    def test_search_failure_is_error_not_no_match(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = dict(variant_id="id", product_title="iPhone 12 Pro", storage="512GB",
                          color="Gold", condition="Excellent")
            with patch("subprocess.run", return_value=types.SimpleNamespace(returncode=1)):
                with self.assertRaises(ValueError):
                    runner.run_chunk(folder, types.SimpleNamespace(model="iPhone 12 Pro", apply=False),
                                     [target], 0, folder / "archive")
            self.assertEqual(support.read_rows(folder / "run_report.csv")[0]["status"], "ERROR")

    def test_failed_model_run_reports_unprocessed_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("run_agent.py", "batch_update.py", "amazon_batch.py", "amazon_verify.py"):
                shutil.copy2(ROOT / name, folder / name)
            for name in ("batch_proposals.py", "shopify_check.py"):
                (folder / name).touch()
            targets = [dict(variant_id=f"gid://shopify/ProductVariant/{100+i}", product_title="iPhone 12 Pro",
                            storage="128GB" if i < 2 else "256GB", color=f"Colour{i}", condition="Excellent", competitor_source="Amazon.ca")
                       for i in range(4)]
            support.write_rows(folder / "competitor_searches.csv", targets, list(targets[0]))
            with patch("subprocess.run", return_value=types.SimpleNamespace(returncode=1)) as process, \
                 patch.object(sys, "argv", ["run_agent.py", "--model", "iPhone 12 Pro", "--all", "--limit", "2"]):
                with self.assertRaises(SystemExit):
                    runpy.run_path(str(folder / "run_agent.py"), run_name="__main__")
            self.assertEqual(process.call_count, 1)
            report = support.read_rows(folder / "run_report.csv")
            self.assertEqual([r["status"] for r in report], ["ERROR", "ERROR", "NOT_PROCESSED", "NOT_PROCESSED"])
            self.assertFalse((folder / "price_agent.lock").exists())

    def test_all_model_uses_small_batches_and_reports_every_target(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("run_agent.py", "batch_update.py", "amazon_batch.py", "amazon_verify.py"):
                shutil.copy2(ROOT / name, folder / name)
            for name in ("batch_proposals.py", "shopify_check.py"):
                (folder / name).touch()
            targets = [dict(variant_id=f"gid://shopify/ProductVariant/{100+i}", product_title="iPhone 12 Pro",
                            storage="128GB", color=f"Colour{i}", condition="Excellent", competitor_source="Amazon.ca")
                       for i in range(4)]
            support.write_rows(folder / "competitor_searches.csv", targets, list(targets[0]))
            searches = []
            def fake_process(command, **kwargs):
                script = Path(command[2]).name
                if script == "amazon_batch.py":
                    start = int(command[command.index("--start") + 1])
                    limit = int(command[command.index("--limit") + 1])
                    searches.append((start, limit))
                    rows = [dict(checked_at=datetime.now().astimezone().isoformat(),
                                 shopify_variant_id=t["variant_id"], target_model=t["product_title"],
                                 target_storage=t["storage"], target_color=t["color"], target_condition=t["condition"],
                                 candidate_url="https://www.amazon.ca/dp/B08PMGDBR4")
                            for t in targets[start:start+limit]]
                    support.write_rows(folder / "amazon_candidates.csv", rows, list(rows[0]))
                elif script == "amazon_verify.py":
                    rows = [dict(checked_at=datetime.now().astimezone().isoformat(),
                                 shopify_variant_id=c["shopify_variant_id"], model=c["target_model"],
                                 storage=c["target_storage"], color=c["target_color"], condition=c["target_condition"],
                                 source_url=c["candidate_url"], status="HOLD", reason="Unavailable")
                            for c in support.read_rows(folder / "amazon_candidates.csv")]
                    support.write_rows(folder / "amazon_verified_offers.csv", rows, list(rows[0]))
                else:
                    raise AssertionError("No verified offer: updater must not run")
                return types.SimpleNamespace(returncode=0)
            with patch("subprocess.run", side_effect=fake_process), \
                 patch.object(sys, "argv", ["run_agent.py", "--model", "iPhone 12 Pro", "--all", "--limit", "2"]):
                runpy.run_path(str(folder / "run_agent.py"), run_name="__main__")
            self.assertEqual(searches, [(0, 2), (2, 2)])
            report = support.read_rows(folder / "run_report.csv")
            self.assertEqual(len(report), 4)
            self.assertTrue(all(r["status"] == "HOLD" for r in report))
            self.assertFalse((folder / "price_agent.lock").exists())

    def test_freshness(self):
        now = datetime.now().astimezone()
        support.fresh(now.isoformat(), now)
        for value in (now - timedelta(minutes=16), now + timedelta(minutes=6)):
            with self.assertRaises(ValueError):
                support.fresh(value.isoformat())
        with self.assertRaises(ValueError):
            support.fresh(now.replace(tzinfo=None).isoformat())
        with self.assertRaises(ValueError):
            support.fresh((now - timedelta(minutes=1)).isoformat(), now)

    def test_approval_requires_exact_metadata(self):
        p = proposal()
        record = {k: p[k] for k in ("variant_id", "model", "storage", "color", "condition")}
        self.assertFalse(support.is_approved(p, set(), []))
        self.assertTrue(support.is_approved(p, set(), [record]))
        record["storage"] = "128GB"
        self.assertFalse(support.is_approved(p, {p["variant_id"]}, [record]))

    def test_no_candidate_report_and_failed_search(self):
        target = dict(variant_id="id", product_title="iPhone 12 Pro", storage="512GB",
                      color="Gold", condition="Excellent")
        row = support.report_rows([target], [], [], [], [], "PREVIEW")[0]
        self.assertEqual(row["status"], "NO_CANDIDATE")
        row = support.report_rows([target], [], [], [], [], "ERROR", "Search interrupted")[0]
        self.assertEqual(row["status"], "ERROR")

    def execute_updater(self, proposals, apply=False, fail_readback=False, drift=False, fallback_source=None):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copy2(ROOT / "batch_update.py", folder / "batch_update.py")
            current = {p["variant_id"]: p["current_shopify_price_cad"] for p in proposals}
            calls, mutation_count, reads = [], 0, 0

            def post(url, **kwargs):
                nonlocal mutation_count, reads
                calls.append(kwargs["json"])
                variables = kwargs["json"]["variables"]
                if "variants" in variables:
                    mutation_count += 1
                    update = variables["variants"][0]
                    current[update["id"]] = update["price"]
                    data = {"productVariantsBulkUpdate": {
                        "productVariants": [update], "userErrors": []}}
                else:
                    reads += 1
                    p = next(p for p in proposals if p["variant_id"] == variables["id"])
                    price = current[p["variant_id"]]
                    if drift and reads > len(proposals):
                        price = "999.00"
                    if fail_readback and mutation_count:
                        price = "0.01"
                    data = {"productVariant": {"id": p["variant_id"], "price": price,
                        "product": {"id": p["product_id"], "title": p["model"], "status": "ACTIVE"},
                        "selectedOptions": [{"name": "Size", "value": p["storage"]},
                            {"name": "Color", "value": p["color"]},
                            {"name": "Condition", "value": "Excellent"}]}}
                return types.SimpleNamespace(status_code=200, json=lambda: {"data": data})

            prepared = types.ModuleType("batch_proposals")
            prepared.proposals = proposals
            prepared.query = "query CheckVariant"
            if fallback_source:
                prepared.source_offers = {p["variant_id"]: fallback_source for p in proposals}
                prepared.validate_source = lambda source: {"product": {"id": proposals[0]["product_id"]}}
            prepared.shopify = types.SimpleNamespace(domain="shop-techgenius.myshopify.com", token="FAKE",
                                                       data={"shop": {"currencyCode": "CAD"}})
            requests = types.ModuleType("requests")
            requests.post = post
            requests.RequestException = type("FakeRequestError", (Exception,), {})
            with patch.dict(sys.modules, {"batch_proposals": prepared, "requests": requests}), \
                 patch.object(sys, "argv", ["batch_update.py"] + (["--apply"] if apply else [])):
                try:
                    runpy.run_path(str(folder / "batch_update.py"), run_name="__main__")
                except SystemExit as error:
                    exit_value = error.code
                else:
                    exit_value = 0
            self.last_mutated_ids = [c["variables"]["variants"][0]["id"] for c in calls
                                     if "variants" in c["variables"]]
            log = folder / "price_update_log.jsonl"
            self.last_audit = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return support.read_rows(folder / "batch_update_results.csv"), mutation_count, exit_value

    def test_fallback_writes_target_and_records_actual_source(self):
        p = proposal()
        p.update(pricing_basis="COLOUR_FALLBACK", source_color="Silver",
                 source_variant_id="gid://shopify/ProductVariant/999")
        source = dict(status="VERIFIED", shopify_variant_id=p["source_variant_id"], color="Silver",
                      model=p["model"], storage=p["storage"], condition=p["condition"],
                      checked_at=p["offer_checked_at"], source_url=p["source_url"], seller="Test Seller",
                      price_cad=p["competitor_price_cad"])
        p["seller"] = "Test Seller"
        rows, mutations, _ = self.execute_updater([p], apply=True, fallback_source=source)
        self.assertEqual(mutations, 1)
        self.assertEqual(rows[0]["status"], "APPLIED_AND_VERIFIED")
        self.assertEqual(self.last_mutated_ids, [p["variant_id"]])
        self.assertEqual(self.last_audit[-1]["source_color"], "Silver")
        self.assertEqual(self.last_audit[-1]["source_variant_id"], p["source_variant_id"])

    def test_tampered_fallback_storage_blocks_write(self):
        p = proposal()
        p.update(pricing_basis="COLOUR_FALLBACK", source_color="Silver",
                 source_variant_id="gid://shopify/ProductVariant/999", seller="Test Seller")
        source = dict(status="VERIFIED", shopify_variant_id=p["source_variant_id"], color="Silver",
                      model=p["model"], storage="128GB", condition=p["condition"],
                      checked_at=p["offer_checked_at"], source_url=p["source_url"], seller="Test Seller",
                      price_cad=p["competitor_price_cad"])
        _, mutations, result = self.execute_updater([p], apply=True, fallback_source=source)
        self.assertEqual(mutations, 0)
        self.assertIn("Invalid colour-fallback provenance", str(result))

    def test_preview_never_mutates(self):
        rows, mutations, result = self.execute_updater([proposal()])
        self.assertEqual((mutations, result, rows[0]["status"]), (0, 0, "PREVIEW"))

    def test_unapproved_target_cannot_write(self):
        rows, mutations, _ = self.execute_updater([proposal("gid://shopify/ProductVariant/999")], apply=True)
        self.assertEqual(mutations, 0)
        self.assertEqual(rows[0]["status"], "HOLD_APPROVAL")

    def test_no_change_does_not_need_write_approval(self):
        p = proposal("gid://shopify/ProductVariant/999", price="626.00")
        p["action"] = "NO CHANGE"
        rows, mutations, _ = self.execute_updater([p], apply=True)
        self.assertEqual((mutations, rows[0]["status"]), (0, "ALREADY_CORRECT"))

    def test_only_approved_changes_apply(self):
        rows, mutations, _ = self.execute_updater([
            proposal(), proposal("gid://shopify/ProductVariant/999", color="Silver")], apply=True)
        self.assertEqual(mutations, 1)
        self.assertEqual({r["status"] for r in rows}, {"APPLIED_AND_VERIFIED", "HOLD_APPROVAL"})

    def test_readback_failure_is_not_success(self):
        rows, mutations, result = self.execute_updater([proposal()], apply=True, fail_readback=True)
        self.assertEqual(mutations, 1)
        self.assertEqual(rows[0]["status"], "UPDATE_UNCONFIRMED")
        self.assertNotEqual(result, 0)

    def test_live_price_drift_blocks_write(self):
        _, mutations, result = self.execute_updater([proposal()], apply=True, drift=True)
        self.assertEqual(mutations, 0)
        self.assertNotEqual(result, 0)

    def test_stale_proposal_blocks_write(self):
        p = proposal()
        p["offer_checked_at"] = (datetime.now().astimezone() - timedelta(minutes=16)).isoformat()
        _, mutations, result = self.execute_updater([p], apply=True)
        self.assertEqual(mutations, 0)
        self.assertNotEqual(result, 0)


class OfferParserTests(unittest.TestCase):
    """Exercise the actual verifier's offer checks with captured text, no browser."""
    def parse(self, section, cart=True):
        tree = ast.parse((ROOT / "amazon_verify.py").read_text(encoding="utf-8"))
        verify = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verify")
        start = next(i for i, n in enumerate(verify.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "price_match" for t in n.targets))
        function = ast.FunctionDef(name="check", args=ast.arguments(posonlyargs=[],
            args=[ast.arg(arg="section")], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=verify.body[start:], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        button = types.SimpleNamespace(is_visible=lambda: cart, is_enabled=lambda: cart)
        buttons = types.SimpleNamespace(count=lambda: 1, nth=lambda i: button)
        namespace = dict(re=re, Decimal=Decimal, title="Test listing", args=types.SimpleNamespace(debug=False),
                         offer_container=types.SimpleNamespace(get_by_role=lambda *a, **k: buttons))
        exec(compile(module, "verifier_offer_checks", "exec"), namespace)
        return namespace["check"](section)

    def section(self):
        return ("Refurbished - Excellent\n$699.99\n$699\n.\n99\n"
                "$5.54 delivery October 28 - 30. Details\nOnly 1 left in stock.\n"
                "Add to cart\nShipper / Seller\nSuperdevice\nCondition\nRefurbished - Excellent\n")

    def test_delivery_fee_does_not_become_item_price(self):
        _, price, seller = self.parse(self.section())
        self.assertEqual((price, seller), (Decimal("699.99"), "Superdevice"))

    def test_second_unlabelled_price_stays_on_hold(self):
        with self.assertRaisesRegex(ValueError, "ambiguous item amounts"):
            self.parse(self.section() + "Another offer $600.00\n")

    def test_no_stock_and_disabled_cart_stays_on_hold(self):
        with self.assertRaisesRegex(ValueError, "availability"):
            self.parse(self.section().replace("Only 1 left in stock.", ""), cart=False)

    def test_enabled_cart_can_confirm_availability(self):
        self.parse(self.section().replace("Only 1 left in stock.", ""), cart=True)

    def test_wrong_grade_stays_on_hold(self):
        with self.assertRaisesRegex(ValueError, "Excellent"):
            self.parse(self.section().replace("Excellent", "Good"))


if __name__ == "__main__":
    unittest.main()
