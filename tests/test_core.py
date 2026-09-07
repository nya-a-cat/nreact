import unittest

from nreact._core import Session, parse


class ProtocolTests(unittest.TestCase):
    def test_dense_unicode_and_multiline_thought(self):
        decision = parse("Thought 1: 查找\n下一步\nAction 1: Search[猫]", 1, "dense")
        self.assertEqual((decision.thought, decision.name, decision.argument), ("查找\n下一步", "search", "猫"))

    def test_sparse_formats(self):
        for value in ["Thought: Plan", "Action: Think[Plan]"]:
            self.assertEqual(parse(value, 2, "sparse").name, "think")
        self.assertEqual(parse("Action: Finish[ok]", 2, "sparse").argument, "ok")

    def test_invalid_formats(self):
        bad = ["Action: Search[x]\nObservation: invented", "Action: Search[x]\nAction: Finish[y]",
               "Action 2: Search[x]", "Action: Search[x", "plain text", "Thought:",
               "Action: Think[]", "Action: Search[x]\ntrailing text"]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse(text, 1, "sparse")
        with self.assertRaises(ValueError):
            parse("Action: Search[x]", 1, "dense")

    def test_json_argument(self):
        payload = '{"items":[1,2],"text":"a\\nb"}'
        self.assertEqual(parse(f"Action: Tool[{payload}]", 1, "sparse").argument, payload)

    def test_action_feedback_order_and_terminal_state(self):
        session = Session(3, "sparse")
        with self.assertRaises(RuntimeError):
            session.observe("unexpected", False)
        session.advance("Action: Search[x]")
        with self.assertRaises(RuntimeError):
            session.advance("Action: Search[y]")
        session.observe("found x", False)
        self.assertIn("Observation 1: found x", session.history)
        session.advance("Action: Finish[done]")
        with self.assertRaises(RuntimeError):
            session.advance("Action: Search[z]")

    def test_invalid_turn_consumes_budget(self):
        session = Session(1, "dense")
        self.assertEqual(session.advance("bad")[0], "invalid")
        self.assertEqual(session.steps, 1)
        with self.assertRaises(RuntimeError):
            session.advance("bad again")

    def test_thought_does_not_need_observation(self):
        session = Session(3, "sparse")
        session.advance("Thought: Plan")
        self.assertNotIn("Observation", session.history)
        session.advance("Action: Search[x]")
        session.observe("won", True)
        self.assertTrue(session.done)

    def test_configuration(self):
        for steps, mode in [(0, "dense"), (2, "unknown")]:
            with self.assertRaises(ValueError):
                Session(steps, mode)
