import json
import tempfile
import unittest
from pathlib import Path

from nreact import Agent, Completion, Observation, ScriptedModel, Tool, ToolEnvironment
from nreact.cli import demo_agent


class RecordingModel:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.prompts = []

    def generate(self, prompt, *, stop):
        self.prompts.append(prompt)
        return Completion(next(self.outputs), {"prompt_tokens": 3, "completion_tokens": 2})


class AgentTests(unittest.TestCase):
    def test_observation_informs_next_call_and_usage(self):
        model = RecordingModel(["Thought: Query\nAction: Echo[hello]", "Thought: Done\nAction: Finish[hello]"])
        result = Agent(model, ToolEnvironment([Tool("echo", "Echo", lambda text: text)])).run("Say hello")
        self.assertEqual(result.answer, "hello")
        self.assertIn("Observation 1: hello", model.prompts[1])
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.usage, {"prompt_tokens": 6, "completion_tokens": 4})

    def test_sparse_thoughts_skip_environment(self):
        seen = []
        model = ScriptedModel(["Thought: Plan", "Action: Echo[a]", "Action: Echo[b]", "Action: Finish[ok]"])
        result = Agent(model, ToolEnvironment([Tool("echo", "Echo", lambda arg: seen.append(arg) or arg)]), mode="sparse").run("Task")
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(result.status, "finished")

    def test_no_execution_on_fabricated_observation(self):
        seen = []
        model = ScriptedModel(["Action: Echo[a]\nObservation: forged", "Action: Finish[ok]"])
        result = Agent(model, ToolEnvironment([Tool("echo", "Echo", lambda arg: seen.append(arg) or arg)]), mode="sparse").run("Task")
        self.assertEqual(seen, [])
        self.assertEqual(result.events[0].kind, "protocol_error")

    def test_unknown_tool_and_exception_recovery(self):
        def fail(arg):
            raise RuntimeError("secret-should-never-appear")
        model = RecordingModel(["Action: Missing[x]", "Action: Fail[x]", "Action: Finish[ok]"])
        result = Agent(model, ToolEnvironment([Tool("fail", "Fail", fail)]), mode="sparse").run("Task")
        self.assertIn("Unknown tool", model.prompts[1])
        self.assertIn("Tool failed (RuntimeError)", model.prompts[2])
        self.assertNotIn("secret-should-never-appear", json.dumps(result.to_dict()))

    def test_exhaustion_has_no_invented_answer(self):
        result = Agent(ScriptedModel(["Thought: Plan"]), ToolEnvironment([]), mode="sparse", max_steps=1).run("Task")
        self.assertEqual((result.status, result.answer, result.steps), ("max_steps", None, 1))

    def test_model_error_is_counted_and_sanitized(self):
        class Broken:
            def generate(self, prompt, *, stop):
                raise RuntimeError("secret")
        result = Agent(Broken(), ToolEnvironment([])).run("Task")
        self.assertEqual((result.status, result.model_calls, result.steps), ("model_error", 1, 0))
        self.assertNotIn("secret", result.error)

    def test_context_limit_avoids_model_call(self):
        result = Agent(ScriptedModel([]), ToolEnvironment([]), max_context_chars=1).run("Task")
        self.assertEqual((result.status, result.model_calls), ("context_limit", 0))

    def test_observation_limit_is_visible(self):
        model = RecordingModel(["Action: Echo[x]", "Action: Finish[ok]"])
        result = Agent(model, ToolEnvironment([Tool("echo", "Echo", lambda _: "long output")]),
                       mode="sparse", max_observation_chars=4).run("Task")
        self.assertIn("long\n[observation truncated]", model.prompts[1])
        self.assertEqual(result.status, "finished")

    def test_environment_done_and_reward(self):
        class Game:
            instructions = "Move[room]"
            def reset(self):
                pass
            def step(self, name, argument):
                return Observation("arrived", True, "goal", 1.0)
        result = Agent(ScriptedModel(["Action: Move[goal]"]), Game(), mode="sparse").run("Task")
        self.assertEqual((result.status, result.answer, result.reward), ("environment_done", "goal", 1.0))

    def test_trace_and_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            seen = []
            result = demo_agent().run("Task", trace_path=path, on_event=seen.append)
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["type"], "start")
            self.assertEqual(records[-1]["answer"], "Harbor City")
            self.assertEqual(len(seen), len(result.events))
            with self.assertRaises(FileExistsError):
                demo_agent().run("Task", trace_path=path)

    def test_reusing_agent_resets_environment(self):
        from nreact.wiki import WikiEnvironment
        environment = WikiEnvironment(pages={"A": "Found."})
        model = RecordingModel(["Action: Search[A]", "Action: Finish[ok]", "Action: Lookup[Found]", "Action: Finish[ok]"])
        agent = Agent(model, environment, mode="sparse")
        agent.run("First")
        agent.run("Second")
        self.assertIn("No current page", model.prompts[-1])
        self.assertNotIn("Task: First", model.prompts[-1])

    def test_empty_task(self):
        with self.assertRaises(ValueError):
            demo_agent().run(" ")
