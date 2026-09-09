import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact._run_store import load_record, write_record


IDENTITY = "a" * 32


def fixture(status="running"):
    return {"id": IDENTITY, "task": "Task", "model": "ScriptedModel", "config": {},
            "started_at": "2026-09-09T12:00:00+00:00", "status": status, "demo": False,
            "steps": 0, "events": [], "result": None, "error": None, "elapsed_seconds": 0}


def event(text="Checking", elapsed=0.1):
    return {"type": "event", "kind": "thought", "step": 1, "text": text, "tool": None,
            "elapsed_seconds": elapsed}


def result(events):
    return {"type": "result", "status": "finished", "answer": "ok", "steps": 1,
            "model_calls": 1, "usage": {"total_tokens": 5}, "events": [
                {key: value for key, value in item.items() if key not in {"type", "elapsed_seconds"}}
                for item in events], "elapsed_seconds": 0.5, "error": None, "reward": None}


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / f"{IDENTITY}.json"
        self.trace = self.root / f"{IDENTITY}.jsonl"

    def journal(self, *entries, tail=b""):
        start = {"type": "start", "schema_version": 1, "task": "Task"}
        self.trace.write_bytes(b"".join(
            (json.dumps(item, ensure_ascii=False) + "\n").encode() for item in (start, *entries)) + tail)

    def test_completed_round_trip(self):
        record = fixture("finished")
        write_record(self.root, record)
        self.assertEqual(load_record(self.root, IDENTITY), record)

    def test_atomic_replace_updates_a_record(self):
        write_record(self.root, fixture())
        completed = fixture("cancelled")
        write_record(self.root, completed)
        self.assertEqual(load_record(self.root, IDENTITY), completed)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_replace_failure_preserves_previous_file_and_cleans_temp(self):
        write_record(self.root, fixture("finished"))
        previous = self.path.read_bytes()
        with patch("nreact._run_store.os.replace", side_effect=OSError("private path")):
            with self.assertRaises(OSError):
                write_record(self.root, fixture("cancelled"))
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_serialization_failure_preserves_previous_file(self):
        write_record(self.root, fixture("finished"))
        previous = self.path.read_bytes()
        record = fixture("finished")
        record["config"]["bad"] = object()
        with self.assertRaises(TypeError):
            write_record(self.root, record)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_write_size_is_bounded_in_utf8_bytes(self):
        record = fixture("finished")
        record["task"] = "猫" * 100
        with patch("nreact._run_store.MAX_RECORD_BYTES", 300):
            with self.assertRaises(ValueError):
                write_record(self.root, record)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_fsync_failure_does_not_publish(self):
        with patch("nreact._run_store.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_record(self.root, fixture())
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "Windows permissions are ACL-based")
    def test_record_is_owner_read_write_only(self):
        write_record(self.root, fixture("finished"))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_manifest_without_trace_is_interrupted(self):
        write_record(self.root, fixture())
        recovered = load_record(self.root, IDENTITY)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIsNone(recovered["result"])
        self.assertIn("not restarted", recovered["error"])

    def test_all_live_states_are_recovered(self):
        for state in ("running", "paused", "pausing", "cancelling"):
            with self.subTest(state=state):
                write_record(self.root, fixture(state))
                self.assertEqual(load_record(self.root, IDENTITY)["status"], "interrupted")

    def test_recovery_retains_complete_events_before_torn_utf8_tail(self):
        write_record(self.root, fixture())
        self.journal(event("猫"), tail=b'{"type":"event","text":"\xe7\x8c')
        recovered = load_record(self.root, IDENTITY)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["steps"], 1)
        self.assertEqual(recovered["events"][0]["text"], "猫")
        self.assertEqual(recovered["events"][0]["index"], 0)
        self.assertEqual(recovered["events"][0]["elapsed_seconds"], 0.1)
        self.assertIn("last readable", recovered["error"])

    def test_journal_result_recovers_completion_without_rerun(self):
        write_record(self.root, fixture())
        self.journal(event(), result([event()]))
        before_manifest, before_trace = self.path.read_bytes(), self.trace.read_bytes()
        recovered = load_record(self.root, IDENTITY)
        self.assertEqual(recovered["status"], "finished")
        self.assertEqual(recovered["result"]["answer"], "ok")
        self.assertEqual(recovered["result"]["usage"]["total_tokens"], 5)
        self.assertEqual(recovered["elapsed_seconds"], 0.5)
        self.assertEqual(self.path.read_bytes(), before_manifest)
        self.assertEqual(self.trace.read_bytes(), before_trace)

    def test_legacy_journal_without_event_timestamps(self):
        write_record(self.root, fixture())
        old = event()
        del old["elapsed_seconds"]
        self.journal(old)
        self.assertEqual(load_record(self.root, IDENTITY)["events"][0]["elapsed_seconds"], 0)

    def test_invalid_middle_entry_stops_replay_at_valid_prefix(self):
        write_record(self.root, fixture())
        self.journal(event(), [], event("must not appear", 0.2))
        recovered = load_record(self.root, IDENTITY)
        self.assertEqual(len(recovered["events"]), 1)
        self.assertEqual(recovered["status"], "interrupted")

    def test_mismatched_task_is_not_replayed(self):
        write_record(self.root, fixture())
        self.trace.write_text(json.dumps({"type": "start", "schema_version": 1, "task": "Other"}) + "\n"
                              + json.dumps(event("private")) + "\n", encoding="utf-8")
        self.assertEqual(load_record(self.root, IDENTITY)["events"], [])

    def test_mismatched_result_is_not_accepted(self):
        write_record(self.root, fixture())
        self.journal(event(), result([event("different")]))
        recovered = load_record(self.root, IDENTITY)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIsNone(recovered["result"])

    def test_trace_read_budget_preserves_valid_prefix(self):
        write_record(self.root, fixture())
        self.journal(event(), event("x" * 1000, 0.2))
        with patch("nreact._run_store.MAX_TRACE_BYTES", 400):
            recovered = load_record(self.root, IDENTITY)
        self.assertEqual(len(recovered["events"]), 1)
        self.assertIn("last readable", recovered["error"])

    def test_invalid_record_shapes_and_metadata_are_rejected(self):
        bad_records = [None, [], "text", {"id": IDENTITY}]
        for field, bad in (("id", "b" * 32), ("task", []), ("started_at", None),
                           ("started_at", "yesterday"), ("started_at", "2026-09-09"),
                           ("status", []), ("steps", True), ("events", {}),
                           ("events", [None]), ("result", []), ("elapsed_seconds", -1)):
            record = fixture("finished")
            record[field] = bad
            bad_records.append(record)
        for record in bad_records:
            with self.subTest(record=record):
                self.path.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_record(self.root, IDENTITY)

    def test_nonfinite_values_anywhere_in_json_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
            raw = json.dumps(fixture("finished")).replace('"config": {}', f'"config": {{"x": {literal}}}')
            self.path.write_text(raw, encoding="utf-8")
            with self.subTest(literal=literal), self.assertRaises(ValueError):
                load_record(self.root, IDENTITY)

    def test_deeply_nested_or_oversized_or_invalid_utf8_record(self):
        for data in (b"[" * 2000 + b"]" * 2000, b"\xff", b"{" * 101):
            self.path.write_bytes(data)
            with patch("nreact._run_store.MAX_RECORD_BYTES", 100):
                with self.assertRaises(ValueError):
                    load_record(self.root, IDENTITY)
        self.path.write_bytes(b"[" * 2000 + b"]" * 2000)
        with self.assertRaises(ValueError):
            load_record(self.root, IDENTITY)

    def test_identifiers_cannot_escape_directory(self):
        for identity in ("../secret", "A" * 32, "", None, [IDENTITY]):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                load_record(self.root, identity)

    def test_symlinked_record_is_rejected(self):
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps(fixture("finished")), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            load_record(self.root, IDENTITY)

    def test_corrupt_terminal_record_does_not_fall_back_to_another_trace(self):
        self.path.write_text("null", encoding="utf-8")
        self.journal(event(), result([event()]))
        with self.assertRaises(ValueError):
            load_record(self.root, IDENTITY)


if __name__ == "__main__":
    unittest.main()
