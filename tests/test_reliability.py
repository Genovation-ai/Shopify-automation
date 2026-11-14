"""Offline recovery checks: no browser or Shopify access."""
import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent_support as support


class SaveTests(unittest.TestCase):
    def test_transient_lock_retries_only_rename(self):
        temporary = Mock()
        temporary.replace.side_effect = [PermissionError(), None]
        with patch.object(support.time, "sleep"):
            support.replace_with_retry(temporary, Path("results.csv"))
        self.assertEqual(temporary.replace.call_count, 2)

    def test_persistent_lock_preserves_old_and_pending_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.csv"
            path.write_text("previous")
            with patch.object(Path, "replace", side_effect=PermissionError()), \
                    patch.object(support.time, "sleep"):
                with self.assertRaisesRegex(PermissionError, "Cannot save results.csv"):
                    support.write_rows(path, [{"status": "verified"}], ["status"])
            self.assertEqual(path.read_text(), "previous")
            self.assertIn("verified", path.with_suffix(".csv.tmp").read_text())

    def test_other_io_errors_do_not_retry(self):
        temporary = Mock()
        temporary.replace.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            support.replace_with_retry(temporary, Path("results.csv"))
        self.assertEqual(temporary.replace.call_count, 1)


class SearchTests(unittest.TestCase):
    def search_function(self, answers, waits):
        tree = ast.parse((ROOT / "amazon_batch.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "search_ready")
        class Timeout(Exception):
            pass
        namespace = {"PlaywrightTimeoutError": Timeout, "input": Mock(side_effect=answers)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "search_ready", "exec"), namespace)
        page = Mock()
        box = page.locator.return_value.first
        box.wait_for.side_effect = [Timeout() if w else None for w in waits]
        box.is_enabled.return_value = True
        box.is_editable.return_value = True
        return namespace, page, box

    def test_premature_enter_returns_to_prompt(self):
        namespace, page, box = self.search_function(["", ""], [True, True, False])
        self.assertIs(namespace["search_ready"](page), box)
        self.assertEqual(namespace["input"].call_count, 2)

    def test_user_can_stop_without_collecting(self):
        namespace, page, _ = self.search_function(["q"], [True])
        with self.assertRaisesRegex(ValueError, "do not verify partial"):
            namespace["search_ready"](page)

    def test_disabled_search_requires_manual_recheck(self):
        namespace, page, box = self.search_function([""], [False, False])
        box.is_enabled.side_effect = [False, True]
        self.assertIs(namespace["search_ready"](page), box)
        namespace["input"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
