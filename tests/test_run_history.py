import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact import Agent, ScriptedModel, ToolEnvironment
from nreact._run_store import write_record
from nreact.config import parse_config
from nreact.runs import MAX_UNSAVED_RUNS, RunHistory


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Run did not reach the expected state.")


def saved_record(identity, timestamp="2026-09-09T12:00:00+00:00"):
    return {"id": identity, "task": "Task", "model": "ScriptedModel", "config": {},
            "started_at": timestamp, "status": "finished", "demo": False, "steps": 0,
            "events": [], "result": None, "error": None, "elapsed_seconds": 0}


def simple_agent():
    return Agent(ScriptedModel(["Thought: Done\nAction: Finish[ok]"]), ToolEnvironment([]))


class RunHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.history = RunHistory(self.root / "runs")
        self.config = parse_config({"model": {"name": "test", "api_key": "fixture-secret"}},
                                   self.root / "nreact.toml", use_environment=False)

    def test_manifest_exists_before_agent_construction(self):
        entered, release = threading.Event(), threading.Event()
        def build(config):
            entered.set()
            release.wait(5)
            return simple_agent()
        with patch("nreact.runs.build_agent", side_effect=build):
            identity = self.history.start(self.config, "Task")["id"]
            try:
                self.assertTrue(entered.wait(5))
                manifest = self.history.directory / f"{identity}.json"
                self.assertTrue(manifest.is_file())
                self.assertEqual(json.loads(manifest.read_text())["status"], "running")
                self.assertNotIn("fixture-secret", manifest.read_text())
                self.assertEqual(self.history.snapshot(identity)["status"], "running")
            finally:
                release.set()
                eventually(lambda: self.history.active is None)

    def test_initial_write_failure_does_not_start_agent(self):
        with patch.object(self.history, "_persist", side_effect=OSError("fixture-secret")), \
                patch("nreact.runs.build_agent") as build:
            with self.assertRaisesRegex(ValueError, "Could not create the run record") as caught:
                self.history.start(self.config, "Task")
        self.assertNotIn("fixture-secret", str(caught.exception))
        build.assert_not_called()
        self.assertIsNone(self.history.active)
        self.assertIsNone(self.history.control)
        self.assertEqual(self.history.records, {})

    def test_completed_runs_are_released_from_memory_and_readable_from_disk(self):
        with patch("nreact.runs.build_agent", side_effect=lambda config: simple_agent()):
            identity = self.history.start(self.config, "Task")["id"]
            eventually(lambda: self.history.active is None)
        self.assertEqual(self.history.records, {})
        self.assertIsNone(self.history.control)
        self.assertEqual(self.history.snapshot(identity)["result"]["answer"], "ok")
        self.assertEqual(RunHistory(self.history.directory).snapshot(identity)["status"], "finished")

    def test_final_save_failure_retains_exportable_record_and_clears_lifecycle(self):
        persist = self.history._persist
        for failure in (OSError("disk full"), ValueError("bad data"), TypeError("bad type")):
            with self.subTest(failure=type(failure).__name__):
                def fail_final(record):
                    if record["status"] == "running":
                        return persist(record)
                    raise failure
                with patch.object(self.history, "_persist", side_effect=fail_final), \
                        patch("nreact.runs.build_agent", return_value=simple_agent()):
                    identity = self.history.start(self.config, "Task")["id"]
                    eventually(lambda: self.history.active is None)
                snapshot = self.history.snapshot(identity)
                self.assertEqual(snapshot["status"], "finished")
                self.assertEqual(snapshot["result"]["answer"], "ok")
                self.assertIn("storage_error", snapshot)
                self.assertIsNone(self.history.control)
                # The complete JSONL result repairs a stale manifest on restart.
                recovered = RunHistory(self.history.directory).snapshot(identity)
                self.assertEqual(recovered["status"], "finished")
                self.assertEqual(recovered["result"]["answer"], "ok")

    def test_unsaved_memory_limit_prevents_more_execution_without_dropping_records(self):
        self.history.records.update({f"{number:032x}": saved_record(f"{number:032x}")
                                     for number in range(MAX_UNSAVED_RUNS)})
        with patch("nreact.runs.build_agent") as build:
            with self.assertRaisesRegex(ValueError, "Too many unsaved runs"):
                self.history.start(self.config, "Task")
        build.assert_not_called()
        self.assertEqual(len(self.history.records), MAX_UNSAVED_RUNS)

    def test_worker_start_failure_is_recorded_and_does_not_stick_active(self):
        with patch("nreact.runs.threading.Thread.start", side_effect=RuntimeError("fixture-secret")), \
                patch("nreact.runs.build_agent") as build:
            with self.assertRaisesRegex(ValueError, "Could not start the run worker"):
                self.history.start(self.config, "Task")
        build.assert_not_called()
        self.assertIsNone(self.history.active)
        self.assertIsNone(self.history.control)
        self.assertEqual(self.history.records, {})
        item = self.history.list()["runs"][0]
        self.assertEqual(item["status"], "error")
        self.assertNotIn("fixture-secret", self.history.snapshot(item["id"])["error"])

    def test_build_failure_is_sanitized_and_persisted(self):
        with patch("nreact.runs.build_agent", side_effect=RuntimeError("fixture-secret")):
            identity = self.history.start(self.config, "Task")["id"]
            eventually(lambda: self.history.active is None)
        record = self.history.snapshot(identity)
        self.assertEqual(record["status"], "error")
        self.assertNotIn("fixture-secret", record["error"])
        self.assertGreaterEqual(record["elapsed_seconds"], 0)
        self.assertIsNone(self.history.control)

    def test_invalid_files_do_not_break_history_list(self):
        self.history.directory.mkdir()
        identity = "a" * 32
        write_record(self.history.directory, saved_record(identity))
        for index, value in enumerate((None, [], {}, {"id": "bad"}, "text")):
            (self.history.directory / f"{index:032x}.json").write_text(json.dumps(value), encoding="utf-8")
        (self.history.directory / "invalid.json").write_text("{}", encoding="utf-8")
        self.assertEqual([item["id"] for item in self.history.list()["runs"]], [identity])

    def test_list_retains_fifty_summaries_and_orders_actual_instants(self):
        self.history.directory.mkdir()
        for number in range(55):
            write_record(self.history.directory, saved_record(f"{number:032x}",
                         f"2026-09-09T12:00:{number:02d}+00:00"))
        items = self.history.list()["runs"]
        self.assertEqual(len(items), 50)
        self.assertEqual(items[0]["id"], f"{54:032x}")
        self.assertNotIn("events", items[0])
        self.assertEqual(self.history.records, {})
        for path in self.history.directory.glob("*.json"):
            path.unlink()
        write_record(self.history.directory, saved_record("a" * 32, "2026-09-09T10:00:00+02:00"))
        write_record(self.history.directory, saved_record("b" * 32, "2026-09-09T09:00:00+00:00"))
        self.assertEqual(self.history.list()["runs"][0]["id"], "b" * 32)

    def test_identifier_collision_does_not_overwrite_existing_data(self):
        self.history.directory.mkdir()
        identity = "a" * 32
        for suffix in ("json", "jsonl"):
            path = self.history.directory / f"{identity}.{suffix}"
            path.write_text("existing content", encoding="utf-8")
            with patch("nreact.runs.uuid.uuid4") as new_id:
                new_id.return_value.hex = identity
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.history.start(self.config, "Task")
            self.assertEqual(path.read_text(), "existing content")
            path.unlink()

    def test_snapshot_cannot_mutate_cached_record(self):
        identity = "a" * 32
        self.history.records[identity] = saved_record(identity)
        snapshot = self.history.snapshot(identity)
        snapshot["config"]["changed"] = True
        self.assertEqual(self.history.records[identity]["config"], {})


if __name__ == "__main__":
    unittest.main()
