"""Evaluate explicit QA JSONL records with per-episode traces and exact match."""

import hashlib
import json
import random
import re
import string
from collections import Counter
from pathlib import Path

from .agent import Agent


def normalize_answer(text: str) -> str:
    text = "".join(char for char in text.lower() if char not in string.punctuation)
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def exact_match(prediction: str, answers: list[str]) -> float:
    return float(any(normalize_answer(prediction) == normalize_answer(answer) for answer in answers))


def evaluate(agent: Agent, dataset: str | Path, output: str | Path, *,
             limit: int = 10, seed: int = 233) -> dict:
    """Shuffle deterministically; retain failures in the metric denominator.

    Input rows: {"id": "...", "task": "...", "answers": ["..."]}.
    This runner does not download benchmark datasets or select a model.
    """
    if limit < 1:
        raise ValueError("Evaluation limit must be positive.")
    raw = Path(dataset).read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    ids: set[str] = set()
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                or not isinstance(record.get("task"), str) or not record["task"].strip()
                or not isinstance(record.get("answers"), list) or not record["answers"]
                or not all(isinstance(answer, str) and answer.strip() for answer in record["answers"])):
            raise ValueError("Each row requires a string id, non-empty task and non-empty string answers.")
        if record["id"] in ids:
            raise ValueError("Dataset ids must be unique.")
        ids.add(record["id"])
    if not records:
        raise ValueError("Dataset is empty.")
    random.Random(seed).shuffle(records)
    selected = records[:limit]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    scores: list[float] = []
    statuses: Counter = Counter()
    calls = 0
    summary = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(), "seed": seed,
        "selected_ids": [record["id"] for record in selected],
        "model": getattr(agent.model, "model", type(agent.model).__name__),
        "mode": agent.mode, "max_steps": agent.max_steps,
    }
    (output / "config.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (output / "results.jsonl").open("x", encoding="utf-8") as writer:
        for index, record in enumerate(selected):
            result = agent.run(record["task"], trace_path=output / f"episode-{index:05}.jsonl")
            score = (exact_match(result.answer, record["answers"])
                     if result.answer is not None and result.status in {"finished", "environment_done"} else 0.0)
            scores.append(score)
            statuses[result.status] += 1
            calls += result.model_calls
            writer.write(json.dumps({"id": record["id"], "em": score, **result.to_dict()}, ensure_ascii=False) + "\n")
            writer.flush()
    summary.update(count=len(scores), exact_match=sum(scores) / len(scores),
                   model_calls=calls, statuses=dict(statuses))
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
