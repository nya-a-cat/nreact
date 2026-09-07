import tempfile
import threading
import time
import unittest
from pathlib import Path

from nreact import Agent, Completion, ScriptedModel, Tool, ToolEnvironment
from nreact.config import parse_config
from nreact.control import RunControl
from nreact.runs import RunHistory


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Run did not reach the expected state.")


class ControlTests(unittest.TestCase):
    def test_single_step_completes_one_turn_and_waits(self):
        seen, results = [], []
        controller = RunControl(paused=True)
        agent = Agent(ScriptedModel(["Thought: Query\nAction: Echo[x]", "Thought: Done\nAction: Finish[x]"]),
                      ToolEnvironment([Tool("echo", "Echo input", lambda text: seen.append(text) or text)]))
        thread = threading.Thread(target=lambda: results.append(agent.run("Task", control=controller)), daemon=True)
        thread.start()
        try:
            self.assertEqual(seen, [])
            controller.command("step")
            eventually(lambda: seen == ["x"] and controller.state == "paused")
            self.assertEqual(results, [])
            controller.command("step")
            thread.join(3)
            self.assertEqual(results[0].answer, "x")
            self.assertEqual(results[0].model_calls, 2)
        finally:
            controller.command("cancel")
            thread.join(3)

    def test_cancel_during_model_call_prevents_tool_side_effect(self):
        entered, release = threading.Event(), threading.Event()
        seen, results = [], []
        class BlockingModel:
            def generate(self, prompt, *, stop):
                entered.set()
                release.wait(3)
                return Completion("Thought: Write\nAction: Echo[x]")
        controller = RunControl()
        agent = Agent(BlockingModel(), ToolEnvironment([Tool("echo", "Echo", lambda text: seen.append(text) or text)]))
        thread = threading.Thread(target=lambda: results.append(agent.run("Task", control=controller)), daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            controller.command("cancel")
            release.set()
            thread.join(3)
            self.assertEqual(seen, [])
            self.assertEqual(results[0].status, "cancelled")
        finally:
            release.set()
            controller.command("cancel")
            thread.join(3)

    def test_pause_and_resume(self):
        controller = RunControl(paused=True)
        result = []
        thread = threading.Thread(target=lambda: result.append(Agent(
            ScriptedModel(["Thought: Done\nAction: Finish[ok]"]), ToolEnvironment([])).run("Task", control=controller)), daemon=True)
        thread.start()
        controller.command("resume")
        thread.join(3)
        self.assertEqual(result[0].answer, "ok")
        with self.assertRaises(ValueError):
            controller.command("step")

    def test_history_persists_replay_without_executing_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            history = RunHistory(path / "runs")
            config = parse_config({"model": {"api_key": "fixture-secret"}}, path / "nreact.toml")
            identity = history.start(config, "", demo=True, single_step=True)["id"]
            try:
                eventually(lambda: history.snapshot(identity)["status"] == "paused")
                self.assertEqual(history.snapshot(identity)["steps"], 1)
                with self.assertRaises(ValueError):
                    history.start(config, "", demo=True)
                history.command(identity, "resume")
                eventually(lambda: history.active is None)
                loaded = RunHistory(path / "runs")
                result = loaded.snapshot(identity)
                self.assertEqual(result["result"]["answer"], "Harbor City")
                times = [event["elapsed_seconds"] for event in result["events"]]
                self.assertGreaterEqual(times[0], 0)
                self.assertEqual(times, sorted(times))
                self.assertNotIn("fixture-secret", (path / "runs" / f"{identity}.json").read_text())
                self.assertEqual(loaded.list()["runs"][0]["id"], identity)
                with self.assertRaises(ValueError):
                    loaded.snapshot("../nreact.toml")
            finally:
                if history.active:
                    history.command(identity, "cancel")
                    eventually(lambda: history.active is None)
