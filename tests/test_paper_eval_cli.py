import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nreact import Agent, ScriptedModel, ToolEnvironment
from nreact._core import parse
from nreact.cli import terminal_text
from nreact.evaluation import evaluate, exact_match
from nreact.paper import paper_examples


class PaperAndCliTests(unittest.TestCase):
    def test_demonstrations_are_packaged_and_parseable(self):
        for name, marker, count in [("hotpotqa", "Question:", 6), ("fever", "Claim:", 3)]:
            text = paper_examples(name)
            self.assertEqual(text.count(marker), count)
            turns = list(re.finditer(r"(Thought (\d+):[\s\S]*?\nAction \2:[^\n]*)", text))
            self.assertGreaterEqual(len(turns), count)
            for match in turns:
                parse(match[1], int(match[2]), "dense")

    def test_metric(self):
        self.assertEqual(exact_match("The Jane Austen!", ["Jane Austen"]), 1)
        self.assertEqual(exact_match("REFUTES", ["SUPPORTS"]), 0)

    def test_evaluation_counts_failures_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "qa.jsonl"
            dataset.write_text('\n'.join(json.dumps({"id": str(i), "task": "Task", "answers": ["yes"]}) for i in range(2)), encoding="utf-8")
            agent = Agent(ScriptedModel(["Action: Finish[yes]", "Thought: wait"]), ToolEnvironment([]), mode="sparse", max_steps=1)
            output = Path(directory) / "results"
            summary = evaluate(agent, dataset, output, limit=2)
            self.assertEqual(summary["exact_match"], 0.5)
            self.assertEqual(summary["statuses"], {"finished": 1, "max_steps": 1})
            self.assertEqual(len(list(output.glob("episode-*.jsonl"))), 2)
            self.assertTrue((output / "config.json").exists())
            with self.assertRaises(FileExistsError):
                evaluate(agent, dataset, output)

    def test_invalid_dataset_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "qa.jsonl"
            dataset.write_text('{"id":"1","task":"hi","answers":[]}', encoding="utf-8")
            output = Path(directory) / "results"
            with self.assertRaises(ValueError):
                evaluate(Agent(ScriptedModel([]), ToolEnvironment([])), dataset, output)
            self.assertFalse(output.exists())

    def test_demo_cli_json(self):
        result = subprocess.run([sys.executable, "-m", "nreact", "demo", "--json"], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["answer"], "Harbor City")
        self.assertEqual(data["status"], "finished")

    def test_terminal_controls_removed(self):
        self.assertNotIn("\x1b", terminal_text("\x1b[2Jhello"))
        self.assertNotIn("\u202e", terminal_text("hello\u202e"))
