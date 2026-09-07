"""Small callable tools and an environment adapter."""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .types import Observation


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    function: Callable[[str], str]


class ToolEnvironment:
    def __init__(self, tools: Iterable[Tool]):
        self.tools: dict[str, Tool] = {}
        for tool in tools:
            name = tool.name.lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in {"think", "finish"}:
                raise ValueError("Tool names must be identifiers; think and finish are reserved.")
            if name in self.tools:
                raise ValueError(f"Duplicate tool name: {name}")
            self.tools[name] = tool

    @property
    def instructions(self) -> str:
        return "\n".join(f"{name}[argument]: {tool.description}" for name, tool in self.tools.items())

    def reset(self) -> None:
        """Stateless tools need no reset; stateful applications can implement Environment."""

    def step(self, name: str, argument: str) -> Observation:
        tool = self.tools.get(name)
        if tool is None:
            return Observation("Unknown tool. Available tools: " + ", ".join(self.tools))
        value = tool.function(argument)
        if not isinstance(value, str):
            raise TypeError("Tool functions must return str.")
        return Observation(value)


def workspace_tools(root: str | Path, *, max_bytes: int = 64_000) -> list[Tool]:
    """Read explicit UTF-8 files and list directories inside a selected root.

    This is a convenience boundary for a trusted local workspace, not an OS sandbox.
    Concurrent filesystem changes require stronger process-level isolation.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or max_bytes < 1:
        raise ValueError("Select a directory and a positive byte limit.")

    def resolve(argument: str) -> Path:
        path = (root / argument).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Path escapes the workspace.")
        return path

    def read(argument: str) -> str:
        path = resolve(argument)
        if not path.is_file():
            raise ValueError("Select a regular file.")
        with path.open("rb") as reader:
            raw = reader.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("File exceeds the byte limit.")
        return raw.decode("utf-8-sig")

    def listing(argument: str) -> str:
        path = resolve(argument or ".")
        entries = []
        for child in path.iterdir():
            if len(entries) == 200:
                entries.append("[listing truncated]")
                break
            entries.append(child.name + ("/" if child.is_dir() else ""))
        return json.dumps(sorted(entries), ensure_ascii=False)

    return [Tool("read", "Read a UTF-8 file by its workspace-relative path.", read),
            Tool("list", "List a workspace-relative directory; use . for the root.", listing)]
