"""Catalogue recovery tests use local fixtures and no external processes."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catalogue_agent as agent
from agent_support import REPORT_FIELDS, read_rows, report_rows, write_rows


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.rows = [dict(competitor_source="Amazon.ca", condition="Excellent",
                         product_title=model, storage=storage, color=color,
                         variant_id=f"gid://shopify/ProductVariant/{index}")
                     for index, (model, storage, color) in enumerate([
                         ("iPhone 11", "64GB", "Black"),
                         ("iPhone 11", "128GB", "Black"),
                         ("iPhone 11", "64GB", "White"),
                         ("iPhone 12", "64GB", "Red")], 1)]
        self.save_catalogue()
        self.args = SimpleNamespace(models=None, resume=False, apply=False, new_run=False, limit=3)

    def save_catalogue(self):
        write_rows(self.folder / "competitor_searches.csv", self.rows, list(self.rows[0]))

    def fake_chunk(self, folder, args, targets, start, archive, indices):
        archive.mkdir(parents=True)
        records = report_rows(targets, [], [], [], [], "PREVIEW")
        for r in records:
            r["status"] = "PREVIEW"
        write_rows(archive / "report.csv", records, REPORT_FIELDS)
        write_rows(folder / "run_report.csv", records, REPORT_FIELDS)

    def checkpoint(self):
        return json.loads((self.folder / agent.CHECKPOINT).read_text())

    def test_plan_keeps_all_peer_colours_and_original_indices(self):
        plan = agent.make_plan(self.folder)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0]["indices"], [0, 2])
        self.assertEqual([t["color"] for t in plan[0]["targets"]], ["Black", "White"])

    def test_unknown_model_fails_offline(self):
        with self.assertRaises(ValueError):
            agent.make_plan(self.folder, ["wrong model"])

    def test_duplicate_cross_model_variant_rejected(self):
        self.rows[-1]["variant_id"] = self.rows[0]["variant_id"]
        self.save_catalogue()
        with self.assertRaisesRegex(ValueError, "Duplicate variant"):
            agent.make_plan(self.folder)

    def test_failure_resume_skips_completed_group_and_reports_every_target(self):
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(args[2])
            if len(calls) == 2:
                raise ValueError("search failed")
            self.fake_chunk(*args, **kwargs)
        with patch.object(agent.run_agent, "run_chunk", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "search failed"):
                agent.execute(self.folder, self.args)
        self.assertEqual(len(read_rows(self.folder / agent.REPORT)), 4)
        self.assertEqual([g["state"] for g in self.checkpoint()["groups"]], ["DONE", "FAILED", "PENDING"])
        self.args.resume = True
        with patch.object(agent.run_agent, "run_chunk", side_effect=self.fake_chunk) as run:
            agent.execute(self.folder, self.args)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[2][0]["storage"], "128GB")
        self.assertEqual(len(read_rows(self.folder / agent.REPORT)), 4)
        self.assertTrue(all(g["state"] == "DONE" for g in self.checkpoint()["groups"]))

    def prepare_failed(self):
        with patch.object(agent.run_agent, "run_chunk", side_effect=ValueError("stop")):
            with self.assertRaises(ValueError):
                agent.execute(self.folder, self.args)

    def test_changed_catalogue_blocks_resume(self):
        self.prepare_failed()
        self.rows[0]["color"] = "Yellow"
        self.save_catalogue()
        self.args.resume = True
        with self.assertRaisesRegex(ValueError, "Catalogue changed"):
            agent.execute(self.folder, self.args)

    def test_preview_cannot_resume_as_apply(self):
        self.prepare_failed()
        self.args.resume = self.args.apply = True
        with self.assertRaisesRegex(ValueError, "original mode"):
            agent.execute(self.folder, self.args)

    def test_unfinished_run_requires_resume_or_explicit_new_run(self):
        self.prepare_failed()
        with self.assertRaisesRegex(ValueError, "unfinished catalogue"):
            agent.execute(self.folder, self.args)

    def test_checkpoint_failure_prevents_external_work(self):
        with patch.object(agent, "save_checkpoint", side_effect=PermissionError("locked")), \
                patch.object(agent.run_agent, "run_chunk") as run:
            with self.assertRaises(PermissionError):
                agent.execute(self.folder, self.args)
            run.assert_not_called()

    def test_apply_hard_crash_blocks_resume(self):
        self.args.apply = True
        self.prepare_failed()
        checkpoint = self.checkpoint()
        checkpoint["groups"][0]["state"] = "RUNNING"
        agent.save_checkpoint(self.folder, checkpoint)
        self.args.resume = True
        with self.assertRaisesRegex(ValueError, "without a checkpoint"):
            agent.execute(self.folder, self.args)

    def test_unconfirmed_update_blocks_apply_resume(self):
        self.args.apply = True
        self.prepare_failed()
        checkpoint = self.checkpoint()
        checkpoint["groups"][0]["records"][0]["status"] = "UPDATE_UNCONFIRMED"
        agent.save_checkpoint(self.folder, checkpoint)
        self.args.resume = True
        with self.assertRaisesRegex(ValueError, "Unconfirmed update"):
            agent.execute(self.folder, self.args)


if __name__ == "__main__":
    unittest.main()
