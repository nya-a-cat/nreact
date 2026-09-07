"""ReAct text protocol and episode state transitions, without external effects."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    thought: str
    name: str
    argument: str


def parse(text: str, step: int, mode: str) -> Decision:
    thought: list[str] = []
    action: str | None = None
    in_thought = False
    seen_thought = False
    for line in filter(None, (line.strip() for line in text.strip().splitlines())):
        match = re.fullmatch(r"(Thought|Action)(?: (\d+))?:\s*(.*)", line)
        if match:
            kind, number, value = match.groups()
            if number is not None and int(number) != step:
                raise ValueError("Use the current step number.")
            if kind == "Thought":
                if seen_thought or action is not None:
                    raise ValueError("Expected one thought before the action.")
                thought.append(value)
                seen_thought, in_thought = True, True
            else:
                if action is not None:
                    raise ValueError("Generate exactly one action per turn.")
                action, in_thought = value, False
        elif line.startswith(("Observation", "Action", "Thought")):
            raise ValueError("Use the text protocol and leave observations to the environment.")
        elif in_thought:
            thought.append(line)
        else:
            raise ValueError("Expected Thought: or Action: followed by tool[argument].")
    reasoning = "\n".join(thought).strip()
    if mode == "dense" and not reasoning:
        raise ValueError("Dense mode requires Thought: before Action:.")
    if action is None:
        if mode == "sparse" and reasoning:
            return Decision(reasoning, "think", "")
        raise ValueError("An Action: is required.")
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\[(.*)\]", action)
    if not match:
        raise ValueError("Use tool[argument] on one line.")
    name, argument = match.groups()
    name = name.lower()
    if name == "think":
        if mode == "dense" or reasoning or not argument.strip():
            raise ValueError("Use a non-empty Think[text] only in a sparse thought-only turn.")
        return Decision(argument, "think", "")
    return Decision(reasoning, name, argument)


class Session:
    """Requires feedback between external actions; every generation uses a step."""

    def __init__(self, max_steps: int, mode: str):
        if max_steps < 1 or mode not in {"dense", "sparse"}:
            raise ValueError("Use max_steps > 0 and mode dense or sparse.")
        self.history = ""
        self.steps = 0
        self.done = False
        self.max_steps = max_steps
        self.mode = mode
        self.pending = False

    def advance(self, text: str) -> tuple[str, str, str, str]:
        if self.done or self.pending or self.steps >= self.max_steps:
            raise RuntimeError("Episode finished, exhausted, or awaiting observation.")
        self.steps += 1
        try:
            decision = parse(text, self.steps, self.mode)
        except ValueError as exc:
            message = str(exc)
            self.history += f"Observation {self.steps}: Protocol error: {message}\n"
            return "invalid", "", message, ""
        if decision.thought:
            self.history += f"Thought {self.steps}: {decision.thought}\n"
        if decision.name == "think":
            kind = "thought"
        else:
            self.history += f"Action {self.steps}: {decision.name}[{decision.argument}]\n"
            if decision.name == "finish":
                self.done, kind = True, "finish"
            else:
                self.pending, kind = True, "action"
        return kind, decision.name, decision.argument, decision.thought

    def observe(self, observation: str, done: bool) -> None:
        if not self.pending or self.done:
            raise RuntimeError("No action is awaiting an observation.")
        self.history += f"Observation {self.steps}: {observation}\n"
        self.pending = False
        self.done = done
