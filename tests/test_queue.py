import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact import Agent, Completion, ScriptedModel, ToolEnvironment
from nreact.config import parse_config
from nreact.runs import MAX_QUEUED_RUNS, RunHistory
from nreact.web import LocalApp


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Queue did not reach the expected state.")


def simple_agent(answer="ok"):
    return Agent(ScriptedModel([f"Thought: Done\nAction: Finish[{answer}]"]), ToolEnvironment([]))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.history = RunHistory(self.root / "runs")
        self.config = parse_config({"model": {"name": "original", "api_key": "fixture-secret"}},
                                   self.root / "nreact.toml", use_environment=False)

    def wait_idle(self):
        eventually(lambda: self.history.active is None and not self.history.state()["queued"])

    def test_fifo_executes_exactly_once_and_releases_inputs(self):
        built = []
        def build(config):
            built.append(config.model.name)
            return simple_agent(config.model.name)
        self.history.queue_command("pause")
        ids = []
        for name in ("first", "second", "third"):
            self.config.model.name = name
            ids.append(self.history.start(self.config, name, queue=True)["id"])
        self.assertEqual(self.history.state()["queued"], ids)
        with patch("nreact.runs.build_agent", side_effect=build):
            self.history.queue_command("resume")
            self.wait_idle()
        self.assertEqual(built, ["first", "second", "third"])
        self.assertEqual(self.history.records, {})
        for identity, name in zip(ids, built):
            record = self.history.snapshot(identity)
            self.assertEqual(record["result"]["answer"], name)
            self.assertEqual(record["result"]["model_calls"], 1)
            self.assertGreaterEqual(record["queued_seconds"], 0)
            self.assertIn("execution_started_at", record)
            self.assertNotIn("fixture-secret", (self.history.directory / f"{identity}.json").read_text())

    def test_configuration_and_secret_values_are_copied_at_submission(self):
        self.history.queue_command("pause")
        identity = self.history.start(self.config, "Task", queue=True)["id"]
        self.config.model.name = "changed"
        self.config.model.api_key = "different-secret"
        self.config.agent.max_steps = 1
        built = []
        def build(config):
            built.append((config.model.name, config.model.api_key, config.agent.max_steps))
            return simple_agent()
        with patch("nreact.runs.build_agent", side_effect=build):
            self.history.queue_command("resume")
            self.wait_idle()
        self.assertEqual(built, [("original", "fixture-secret", 20)])
        self.assertEqual(self.history.snapshot(identity)["config"]["model"]["name"], "original")

    def test_cancel_queued_task_never_constructs_model(self):
        self.history.queue_command("pause")
        identity = self.history.start(self.config, "Never run", queue=True)["id"]
        with patch("nreact.runs.build_agent") as build:
            self.assertEqual(self.history.command(identity, "cancel"), {"status": "cancelled"})
            self.history.queue_command("resume")
        build.assert_not_called()
        record = self.history.snapshot(identity)
        self.assertEqual(record["status"], "cancelled")
        self.assertIsNone(record["result"])
        self.assertEqual(record["events"], [])
        self.assertEqual(record["steps"], 0)
        self.assertFalse((self.history.directory / f"{identity}.jsonl").exists())
        self.assertEqual(self.history.records, {})

    def test_paused_queue_admits_work_without_starting_it(self):
        self.history.queue_command("pause")
        with patch("nreact.runs.build_agent") as build:
            identity = self.history.start(self.config, "Wait", queue=True)["id"]
        build.assert_not_called()
        self.assertIsNone(self.history.active)
        self.assertEqual(self.history.snapshot(identity)["status"], "queued")
        self.assertTrue(self.history.list()["queue_paused"])
        self.assertEqual(self.history.list()["queued"], [identity])

    def test_queue_starts_immediately_when_idle(self):
        with patch("nreact.runs.build_agent", return_value=simple_agent()):
            identity = self.history.start(self.config, "Task", queue=True)["id"]
            self.wait_idle()
        self.assertEqual(self.history.snapshot(identity)["status"], "finished")

    def test_new_server_recovers_pending_manifest_without_execution(self):
        self.history.queue_command("pause")
        identity = self.history.start(self.config, "Task", queue=True)["id"]
        with patch("nreact.runs.build_agent") as build:
            restarted = RunHistory(self.history.directory)
            self.assertEqual(restarted.snapshot(identity)["status"], "interrupted")
            restarted.queue_command("resume")
        build.assert_not_called()
        self.assertEqual(restarted.state()["queued"], [])
        self.assertEqual(self.history.snapshot(identity)["status"], "queued")

    def test_admission_limit_preserves_existing_queue(self):
        self.history.queue_command("pause")
        for number in range(MAX_QUEUED_RUNS):
            self.history.start(self.config, f"Task {number}", queue=True)
        with self.assertRaisesRegex(ValueError, "Queue is full"):
            self.history.start(self.config, "Overflow", queue=True)
        self.assertEqual(len(self.history.state()["queued"]), MAX_QUEUED_RUNS)
        self.assertEqual(len(list(self.history.directory.glob("*.json"))), MAX_QUEUED_RUNS)

    def test_initial_storage_failure_cannot_admit_work(self):
        with patch.object(self.history, "_persist", side_effect=OSError("fixture-secret")), \
                patch("nreact.runs.build_agent") as build:
            with self.assertRaisesRegex(ValueError, "Could not create the run record"):
                self.history.start(self.config, "Task", queue=True)
        build.assert_not_called()
        self.assertEqual(self.history.records, {})
        self.assertEqual(self.history.state()["queued"], [])

    def test_worker_failure_pauses_remaining_queue(self):
        self.history.queue_command("pause")
        ids = [self.history.start(self.config, "Task", queue=True)["id"] for _ in range(2)]
        with patch("nreact.runs.threading.Thread.start", side_effect=RuntimeError("fixture-secret")):
            state = self.history.queue_command("resume")
        self.assertTrue(state["queue_paused"])
        self.assertEqual(state["queued"], ids[1:])
        self.assertIsNone(state["active"])
        self.assertEqual(self.history.snapshot(ids[0])["status"], "error")
        self.assertNotIn("fixture-secret", self.history.snapshot(ids[0])["error"])
        with patch("nreact.runs.build_agent", return_value=simple_agent()):
            self.history.queue_command("resume")
            self.wait_idle()
        self.assertEqual(self.history.snapshot(ids[1])["status"], "finished")

    def test_model_failure_pauses_remaining_queue(self):
        class FailedModel:
            def generate(self, prompt, *, stop):
                raise RuntimeError("fixture-secret")
        self.history.queue_command("pause")
        ids = [self.history.start(self.config, "Task", queue=True)["id"] for _ in range(2)]
        failed = Agent(FailedModel(), ToolEnvironment([]))
        with patch("nreact.runs.build_agent", return_value=failed) as build:
            self.history.queue_command("resume")
            eventually(lambda: self.history.active is None)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(self.history.snapshot(ids[0])["status"], "model_error")
        self.assertEqual(self.history.snapshot(ids[1])["status"], "queued")
        self.assertTrue(self.history.queue_paused)

    def test_failed_final_save_pauses_queue_and_retains_result(self):
        self.history.queue_command("pause")
        ids = [self.history.start(self.config, "Task", queue=True)["id"] for _ in range(2)]
        with patch.object(self.history, "_persist", side_effect=OSError("disk full")), \
                patch("nreact.runs.build_agent", return_value=simple_agent()) as build:
            self.history.queue_command("resume")
            eventually(lambda: self.history.active is None)
        self.assertEqual(build.call_count, 1)
        self.assertTrue(self.history.queue_paused)
        self.assertIn("storage_error", self.history.snapshot(ids[0]))
        self.assertEqual(self.history.snapshot(ids[0])["result"]["answer"], "ok")
        self.assertEqual(self.history.state()["queued"], ids[1:])
        recovered = RunHistory(self.history.directory).snapshot(ids[0])
        self.assertEqual(recovered["status"], "finished")

    def test_stop_active_pauses_queue_until_explicit_resume(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingModel:
            def generate(self, prompt, *, stop):
                entered.set()
                release.wait(5)
                return Completion("Thought: Done\nAction: Finish[first]")
        with patch("nreact.runs.build_agent", side_effect=[Agent(BlockingModel(), ToolEnvironment([])), simple_agent("second")]):
            first = self.history.start(self.config, "First")["id"]
            try:
                self.assertTrue(entered.wait(5))
                second = self.history.start(self.config, "Second", queue=True)["id"]
                self.history.command(first, "cancel")
                release.set()
                eventually(lambda: self.history.active is None)
                self.assertEqual(self.history.snapshot(first)["status"], "cancelled")
                self.assertEqual(self.history.snapshot(second)["status"], "queued")
                self.history.queue_command("resume")
                self.wait_idle()
                self.assertEqual(self.history.snapshot(second)["result"]["answer"], "second")
            finally:
                release.set()
                if self.history.active:
                    self.history.command(self.history.active, "cancel")
                    eventually(lambda: self.history.active is None)

    def test_pause_queue_keeps_active_run_alive(self):
        identity = self.history.start(self.config, "", demo=True, single_step=True)["id"]
        try:
            eventually(lambda: self.history.snapshot(identity)["status"] == "paused")
            self.history.queue_command("pause")
            second = self.history.start(self.config, "", demo=True, queue=True)["id"]
            self.history.command(identity, "resume")
            eventually(lambda: self.history.active is None)
            self.assertEqual(self.history.snapshot(identity)["status"], "finished")
            self.assertEqual(self.history.snapshot(second)["status"], "queued")
        finally:
            if self.history.active:
                self.history.command(self.history.active, "cancel")
                eventually(lambda: self.history.active is None)

    def test_invalid_queue_actions_and_step_combination(self):
        for value in (1, "yes", [], None):
            with self.assertRaises(ValueError):
                self.history.start(self.config, "Task", queue=value)
        with self.assertRaises(ValueError):
            self.history.start(self.config, "Task", queue=True, single_step=True)
        with self.assertRaises(ValueError):
            self.history.queue_command("clear")
        self.history.queue_command("pause")
        identity = self.history.start(self.config, "Task", queue=True)["id"]
        for action in ("pause", "resume", "step"):
            with self.assertRaises(ValueError):
                self.history.command(identity, action)
        with self.assertRaises(ValueError):
            self.history.start(self.config, "Cannot skip queue")

    def test_concurrent_admission_is_unique_and_bounded(self):
        self.history.queue_command("pause")
        identities, errors = [], []
        lock = threading.Lock()
        def submit():
            try:
                identity = self.history.start(self.config, "Task", queue=True)["id"]
                with lock:
                    identities.append(identity)
            except Exception as error:
                with lock:
                    errors.append(error)
        threads = [threading.Thread(target=submit) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(len(set(identities)), 12)
        self.assertEqual(set(self.history.state()["queued"]), set(identities))

    def test_pending_manifest_never_contains_credentials(self):
        self.history.queue_command("pause")
        identity = self.history.start(self.config, "Task", queue=True)["id"]
        content = (self.history.directory / f"{identity}.json").read_text(encoding="utf-8")
        self.assertNotIn("fixture-secret", content)
        self.assertNotIn('"api_key":', content)
        self.assertEqual(json.loads(content)["status"], "queued")


class QueueAppTests(unittest.TestCase):
    def test_api_options_are_validated_before_queue_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory) / "nreact.toml")
            for value in (None, 1, [], "true"):
                with self.assertRaises(ValueError):
                    app.start({"demo": True, "queue": value})
            with self.assertRaises(ValueError):
                app.start({"demo": True, "queue": True, "single_step": True})
            self.assertEqual(app.runs.state()["queued"], [])


if __name__ == "__main__":
    unittest.main()
