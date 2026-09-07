"""Command-line entry point for offline demonstration and configured agents."""

import argparse
import json
import sys
import unicodedata

from .agent import Agent
from .evaluation import evaluate
from .models import ChatModel, ScriptedModel
from .paper import paper_examples
from .tools import ToolEnvironment, workspace_tools
from .types import Event
from .wiki import WikiEnvironment


def terminal_text(text: str) -> str:
    return "".join(char for char in text if char in "\n\t" or not unicodedata.category(char).startswith("C"))


def show_event(event: Event) -> None:
    label = f"{event.kind.capitalize()} {event.step}"
    text = f"{event.tool}[{event.text}]" if event.kind == "action" else event.text
    print(terminal_text(f"{label}: {text}"), file=sys.stderr, flush=True)


def demo_agent() -> Agent:
    return Agent(ScriptedModel([
        "Thought 1: Find the designer in the telescope page.\nAction 1: Search[Lumen telescope]",
        "Thought 2: Read the designer's birthplace.\nAction 2: Search[Mira Chen]",
        "Thought 3: The page gives the birthplace as Harbor City.\nAction 3: Finish[Harbor City]",
    ]), WikiEnvironment(pages={
        "Lumen telescope": "The Lumen telescope was designed by Mira Chen. It opened in 2001.",
        "Mira Chen": "Mira Chen designed the Lumen telescope. Mira Chen was born in Harbor City.",
    }), max_steps=7)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nreact", description="A small, pure Python ReAct agent.")
    root.add_argument("--version", action="version", version="nreact 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run a scripted, fictional offline example.")
    demo.add_argument("--trace", help="Create a new JSONL trace file.")
    demo.add_argument("--json", action="store_true", help="Print result JSON.")
    for name in ("run", "eval"):
        command = commands.add_parser(name, help="Run an agent." if name == "run" else "Evaluate a QA JSONL dataset.")
        command.add_argument("--model", help="Model name; defaults to NREACT_MODEL.")
        command.add_argument("--mode", choices=["dense", "sparse"], default="dense")
        command.add_argument("--max-steps", type=int, default=20)
        command.add_argument("--max-tokens", type=int, default=512)
        command.add_argument("--timeout", type=float, default=60)
        command.add_argument("--no-stop", action="store_true", help="Omit stop for servers that reject it.")
        command.add_argument("--paper", choices=["hotpotqa", "fever"], help="Load the authors' few-shot examples.")
        if name == "run":
            command.add_argument("task")
            command.add_argument("--workspace", help="Enable read/list in this directory instead of Wikipedia.")
            command.add_argument("--trace", help="Create a new JSONL trace file.")
            command.add_argument("--json", action="store_true", help="Print result JSON; suppress progress.")
        else:
            command.add_argument("dataset")
            command.add_argument("--output", required=True, help="Create a new output directory.")
            command.add_argument("--limit", type=int, default=10)
            command.add_argument("--seed", type=int, default=233)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "demo":
            if not arguments.json:
                print("Offline scripted example with fictional pages; no model inference.", file=sys.stderr)
            result = demo_agent().run("Where was the designer of the Lumen telescope born?",
                                      trace_path=arguments.trace,
                                      on_event=None if arguments.json else show_event)
        else:
            if getattr(arguments, "workspace", None) and arguments.paper:
                raise ValueError("Paper QA examples require the Wikipedia environment.")
            model = ChatModel.from_env(arguments.model, timeout=arguments.timeout,
                                        max_tokens=arguments.max_tokens, send_stop=not arguments.no_stop)
            environment = (ToolEnvironment(workspace_tools(arguments.workspace))
                           if getattr(arguments, "workspace", None) else WikiEnvironment())
            agent = Agent(model, environment, mode=arguments.mode, max_steps=arguments.max_steps,
                          examples=paper_examples(arguments.paper) if arguments.paper else "")
            if arguments.command == "eval":
                summary = evaluate(agent, arguments.dataset, arguments.output,
                                   limit=arguments.limit, seed=arguments.seed)
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            result = agent.run(arguments.task, trace_path=arguments.trace,
                               on_event=None if arguments.json else show_event)
        if arguments.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(terminal_text(result.answer or f"Stopped: {result.status}"))
            print(f"{result.status}; {result.model_calls} model calls; {result.steps} steps", file=sys.stderr)
        return 0 if result.status in {"finished", "environment_done"} else 1
    except (ValueError, OSError) as exc:
        print(terminal_text(f"nreact: {exc}"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("nreact: interrupted", file=sys.stderr)
        return 130
