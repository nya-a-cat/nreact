"""Command-line entry point for offline demonstration and configured agents."""

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from .agent import Agent
from ._credentials import AuthError
from .evaluation import evaluate
from .models import ScriptedModel
from .config import build_agent, load_config, parse_config, save_config
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
    root.add_argument("--version", action="version", version="nreact 0.3.0")
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run a scripted, fictional offline example.")
    demo.add_argument("--trace", help="Create a new JSONL trace file.")
    demo.add_argument("--json", action="store_true", help="Print result JSON.")
    init = commands.add_parser("init", help="Create a local TOML configuration.")
    init.add_argument("--config", default="nreact.toml", help="New configuration path.")
    ui = commands.add_parser("ui", help="Open the local agent workbench.")
    ui.add_argument("--config", default="nreact.toml", help="Configuration path.")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    auth = commands.add_parser("auth", help="Manage nreact's OpenAI OAuth credentials.")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    for action in ("login", "status", "logout"):
        auth_command = auth_commands.add_parser(action, help={
            "login": "Sign in with ChatGPT using OAuth.",
            "status": "Inspect the local cache without network requests.",
            "logout": "Clear only the nreact credential cache.",
        }[action])
        auth_command.add_argument("--auth-file", help="Credential cache path; defaults to NREACT_AUTH_FILE or ~/.nreact/openai-auth.json.")
        if action == "login":
            auth_command.add_argument("--device", "--device-auth", action="store_true", help="Use a device code instead of a localhost callback.")
            auth_command.add_argument("--no-browser", action="store_true", help="Print the browser login URL without opening it.")
            auth_command.add_argument("--timeout", type=float, default=900, help="Login timeout in seconds (30-900).")
        else:
            auth_command.add_argument("--json", action="store_true", help="Print credential-free status JSON.")
    for name in ("run", "eval"):
        command = commands.add_parser(name, help="Run an agent." if name == "run" else "Evaluate a QA JSONL dataset.")
        command.add_argument("--config", help="TOML file; automatically uses ./nreact.toml when present.")
        command.add_argument("--model", help="Override the configured model name.")
        command.add_argument("--base-url", help="Override the configured model endpoint.")
        command.add_argument("--auth", choices=["api_key", "chatgpt"], help="Select the API-key or ChatGPT OAuth adapter.")
        command.add_argument("--auth-file", help="Override the nreact OAuth cache path, relative to the current directory.")
        command.add_argument("--mode", choices=["dense", "sparse"])
        command.add_argument("--max-steps", type=int)
        command.add_argument("--max-tokens", type=int)
        command.add_argument("--timeout", type=float)
        command.add_argument("--no-stop", dest="send_stop", action="store_const", const=False, default=None,
                             help="Omit stop for servers that reject it.")
        command.add_argument("--paper", choices=["hotpotqa", "fever", "none"], help="Select few-shot examples.")
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


def _auth_command(arguments) -> int:
    from .auth import AuthStore, login

    store = AuthStore(arguments.auth_file)
    if arguments.auth_command == "login":
        def announce(url: str, code: str | None) -> None:
            print(terminal_text(f"Open this URL to sign in with ChatGPT:\n{url}"), flush=True)
            if code:
                print(terminal_text(f"One-time device code: {code}"), flush=True)
        login(auth_file=store.path, device=arguments.device, open_browser=not arguments.no_browser,
              timeout=arguments.timeout, on_authorize=announce)
        print(terminal_text(f"Saved nreact OpenAI credentials to {store.path}"))
        return 0
    if arguments.auth_command == "logout":
        cleared = store.logout()
        if arguments.json:
            print(json.dumps({"cleared": cleared, "path": str(store.path)}))
        else:
            print("Cleared nreact OpenAI credentials." if cleared else "No nreact OpenAI credentials were cached.")
        return 0
    status = store.status()
    if arguments.json:
        print(json.dumps(status, indent=2))
    else:
        message = {"signed_out": "No nreact OpenAI credentials are cached.",
                   "cached": "ChatGPT OAuth credentials are cached locally.",
                   "refresh_required": "ChatGPT OAuth credentials are cached; refresh is required on the next model call."}
        print(message[status["state"]])
        print(terminal_text(f"Cache: {store.path}\nStatus is local; no request was made to OpenAI."))
    return 1 if status["state"] == "signed_out" else 0


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "auth":
            return _auth_command(arguments)
        if arguments.command == "init":
            config = parse_config({}, arguments.config)
            save_config(config)
            print(f"Created {config.path}")
            return 0
        if arguments.command == "ui":
            from .web import serve
            serve(arguments.config, port=arguments.port, open_browser=not arguments.no_browser)
            return 0
        if arguments.command == "demo":
            if not arguments.json:
                print("Offline scripted example with fictional pages; no model inference.", file=sys.stderr)
            result = demo_agent().run("Where was the designer of the Lumen telescope born?",
                                      trace_path=arguments.trace,
                                      on_event=None if arguments.json else show_event)
        else:
            config = load_config(arguments.config or "nreact.toml", missing_ok=arguments.config is None)
            data = config.to_dict()
            for flag, field in (("model", "name"), ("base_url", "base_url"), ("max_tokens", "max_tokens"),
                                ("timeout", "timeout"), ("send_stop", "send_stop"), ("auth", "auth")):
                value = getattr(arguments, flag)
                if value is not None:
                    data["model"][field] = value
            if arguments.auth_file is not None:
                data["model"]["auth_file"] = str(Path(arguments.auth_file).expanduser().absolute())
            for flag in ("mode", "max_steps", "paper"):
                value = getattr(arguments, flag)
                if value is not None:
                    data["agent"][flag] = "" if value == "none" else value
            if getattr(arguments, "workspace", None) is not None:
                data["tools"]["workspace"] = str(Path(arguments.workspace).absolute())
                data["tools"]["wikipedia"] = False
            agent = build_agent(parse_config(data, config.path, use_environment=False))
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
    except (ValueError, OSError, AuthError) as exc:
        print(terminal_text(f"nreact: {exc}"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("nreact: interrupted", file=sys.stderr)
        return 130
