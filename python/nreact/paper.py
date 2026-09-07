"""Pinned demonstrations from the authors' MIT-licensed ReAct repository."""

import json
from importlib.resources import files

UPSTREAM_COMMIT = "6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9"


def paper_examples(task: str = "hotpotqa") -> str:
    if task not in {"hotpotqa", "fever"}:
        raise ValueError("Choose hotpotqa or fever.")
    data = json.loads(files("nreact").joinpath("prompts/paper.json").read_text(encoding="utf-8"))
    return data[task]
