import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from nreact import Agent, Completion, Observation, ScriptedModel, ToolEnvironment
from nreact.control import RunControl


class AgentLifecycleTests(unittest.TestCase):
    def test_precancelled_run_skips_environment_reset_and_model(self):
        environment = Mock(instructions="Echo[input]")
        model = Mock()
        control = RunControl()
        control.command("cancel")
        result = Agent(model, environment).run("Task", control=control)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.model_calls, 0)
        environment.reset.assert_not_called()
        environment.step.assert_not_called()
        model.generate.assert_not_called()

    def test_reset_error_is_sanitized_and_finishes_trace(self):
        environment = Mock(instructions="Echo[input]")
        environment.reset.side_effect = RuntimeError("fixture-secret")
        model = Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            result = Agent(model, environment).run("Task", trace_path=path)
            text = path.read_text(encoding="utf-8")
            entries = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(result.status, "environment_error")
        self.assertEqual(result.steps, 0)
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(entries[-1]["type"], "result")
        self.assertEqual(entries[-1]["status"], "environment_error")
        self.assertEqual(entries[1]["step"], 0)
        self.assertNotIn("fixture-secret", text)
        model.generate.assert_not_called()
        environment.step.assert_not_called()

    def test_cancel_during_last_tool_preserves_observation_and_reports_cancelled(self):
        for done in (False, True):
            with self.subTest(environment_done=done):
                entered, release = threading.Event(), threading.Event()
                results = []
                environment = Mock(instructions="Echo[input]")
                def step(name, argument):
                    entered.set()
                    release.wait(5)
                    return Observation("Tool completed", done=done, answer="ok" if done else None)
                environment.step.side_effect = step
                model = Mock()
                model.generate.return_value = Completion("Thought: Query\nAction: Echo[x]", {"total_tokens": 7})
                control = RunControl()
                agent = Agent(model, environment, max_steps=1)
                thread = threading.Thread(target=lambda: results.append(agent.run("Task", control=control)), daemon=True)
                thread.start()
                try:
                    self.assertTrue(entered.wait(5))
                    control.command("cancel")
                    release.set()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(len(results), 1)
                    self.assertEqual(results[0].status, "cancelled")
                    self.assertIsNone(results[0].answer)
                    self.assertEqual(results[0].model_calls, 1)
                    self.assertEqual(results[0].usage, {"total_tokens": 7})
                    self.assertEqual(results[0].events[-1].kind, "observation")
                    self.assertEqual(results[0].events[-1].text, "Tool completed")
                    environment.step.assert_called_once_with("echo", "x")
                finally:
                    control.command("cancel")
                    release.set()
                    thread.join(5)

    def test_cancel_at_finish_event_is_respected(self):
        control = RunControl()
        def callback(event):
            if event.kind == "action":
                control.command("cancel")
        agent = Agent(ScriptedModel(["Thought: Done\nAction: Finish[ok]"]), ToolEnvironment([]))
        result = agent.run("Task", control=control, on_event=callback)
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.answer)

    def test_cancel_at_last_protocol_error_is_respected(self):
        control = RunControl()
        def callback(event):
            if event.kind == "protocol_error":
                control.command("cancel")
        agent = Agent(ScriptedModel(["invalid response"]), ToolEnvironment([]), max_steps=1)
        self.assertEqual(agent.run("Task", control=control, on_event=callback).status, "cancelled")

    def test_event_timing_is_available_in_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            Agent(ScriptedModel(["Thought: Done\nAction: Finish[ok]"]), ToolEnvironment([])).run("Task", trace_path=path)
            events = [entry for entry in map(json.loads, path.read_text().splitlines()) if entry["type"] == "event"]
        self.assertTrue(events)
        times = [entry["elapsed_seconds"] for entry in events]
        self.assertTrue(all(seconds >= 0 for seconds in times))
        self.assertEqual(times, sorted(times))

    @unittest.skipIf(os.name == "nt", "Windows permissions are ACL-based")
    def test_trace_is_owner_read_write_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            Agent(ScriptedModel(["Thought: Done\nAction: Finish[ok]"]), ToolEnvironment([])).run("Task", trace_path=path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_trace_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("existing", encoding="utf-8")
            environment = Mock(instructions="Echo[input]")
            with self.assertRaises(FileExistsError):
                Agent(Mock(), environment).run("Task", trace_path=path)
            self.assertEqual(path.read_text(), "existing")
            environment.reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
