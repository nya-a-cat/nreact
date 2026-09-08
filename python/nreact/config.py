"""Shared TOML configuration for the CLI and Python applications."""

import hashlib
import importlib
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .agent import Agent
from .chatgpt import ChatGPTModel
from .models import ChatModel
from .paper import paper_examples
from .tools import Tool, ToolEnvironment, workspace_tools
from .types import Observation
from .wiki import WikiEnvironment

MAX_CONFIG_BYTES = 65_536


@dataclass
class ModelSettings:
    name: str = ""
    base_url: str = "http://127.0.0.1:8080/v1"
    api_key_env: str = "NREACT_API_KEY"
    api_key: str = field(default="", repr=False)
    max_tokens: int = 512
    temperature: float = 0.0
    timeout: float = 60.0
    send_stop: bool = True
    auth: str = "api_key"
    auth_file: str = ""


@dataclass
class AgentSettings:
    mode: str = "dense"
    max_steps: int = 20
    max_context_chars: int = 100_000
    max_observation_chars: int = 12_000
    paper: str = ""


@dataclass
class CustomTool:
    name: str
    description: str
    callable: str


@dataclass
class ToolSettings:
    wikipedia: bool = True
    workspace: str = ""
    custom: list[CustomTool] = field(default_factory=list)


@dataclass
class Config:
    model: ModelSettings = field(default_factory=ModelSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    path: Path = field(default_factory=lambda: Path("nreact.toml").absolute())

    def to_dict(self) -> dict:
        return {"model": asdict(self.model), "agent": asdict(self.agent), "tools": asdict(self.tools)}

    def public_dict(self) -> dict:
        data = self.to_dict()
        data["model"].pop("api_key")
        return data

    def api_key(self) -> str | None:
        return self.model.api_key or os.getenv(self.model.api_key_env) or None


def _table(data: dict, name: str, allowed: set[str]) -> dict:
    table = data.get(name, {})
    if not isinstance(table, dict):
        raise ValueError(f"{name} must be a table.")
    if set(table) - allowed:
        raise ValueError(f"Unknown fields in {name}. Allowed: {', '.join(sorted(allowed))}.")
    return table


def _string(value, name: str, limit: int = 4096, *, empty: bool = True) -> None:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{name} must be {'a' if empty else 'a non-empty'} string of at most {limit} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be a single line without control characters.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"{name} must contain valid Unicode.") from None


def _number(value, name: str, low: float, high: float, *, integer: bool = False) -> None:
    kinds = (int,) if integer else (int, float)
    if type(value) not in kinds or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be {'an integer' if integer else 'a number'} between {low:g} and {high:g}.")


def parse_config(data: dict, path: str | Path = "nreact.toml", *, use_environment: bool = True) -> Config:
    """Validate configuration without importing tools or making network requests."""
    if not isinstance(data, dict) or set(data) - {"model", "agent", "tools"}:
        raise ValueError("Configuration supports only model, agent and tools tables.")
    defaults = ModelSettings()
    if use_environment:
        defaults.name = os.getenv("NREACT_MODEL", "")
        defaults.base_url = os.getenv("NREACT_BASE_URL", defaults.base_url)
        defaults.auth = os.getenv("NREACT_AUTH", defaults.auth)
    model = ModelSettings(**{**asdict(defaults), **_table(data, "model", set(asdict(defaults)))})
    agent = AgentSettings(**_table(data, "agent", set(asdict(AgentSettings()))))
    tool_data = _table(data, "tools", {"wikipedia", "workspace", "custom"})
    custom = tool_data.get("custom", [])
    if not isinstance(custom, list) or len(custom) > 32:
        raise ValueError("tools.custom must contain at most 32 tool definitions.")
    tools = ToolSettings(wikipedia=tool_data.get("wikipedia", True), workspace=tool_data.get("workspace", ""))
    _string(model.name, "model.name", 256)
    _string(model.base_url, "model.base_url", empty=False)
    _string(model.api_key, "model.api_key", 16_384)
    _string(model.api_key_env, "model.api_key_env", 128)
    _string(model.auth, "model.auth", 32, empty=False)
    _string(model.auth_file, "model.auth_file")
    if model.auth not in {"api_key", "chatgpt"}:
        raise ValueError("model.auth must be api_key or chatgpt.")
    if model.api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", model.api_key_env):
        raise ValueError("model.api_key_env must be an environment-variable name.")
    _number(model.max_tokens, "model.max_tokens", 1, 131_072, integer=True)
    _number(model.timeout, "model.timeout", 1, 300)
    _number(model.temperature, "model.temperature", 0, 2)
    if type(model.send_stop) is not bool:
        raise ValueError("model.send_stop must be true or false.")
    # Reuse the adapter's endpoint rules without connecting to it.
    ChatModel(model.name or "unconfigured", base_url=model.base_url, timeout=model.timeout, max_tokens=model.max_tokens)
    _string(agent.mode, "agent.mode", 16)
    _string(agent.paper, "agent.paper", 16)
    if agent.mode not in {"dense", "sparse"}:
        raise ValueError("agent.mode must be dense or sparse.")
    if agent.paper not in {"", "hotpotqa", "fever"}:
        raise ValueError("agent.paper must be empty, hotpotqa or fever.")
    _number(agent.max_steps, "agent.max_steps", 1, 1000, integer=True)
    _number(agent.max_context_chars, "agent.max_context_chars", 1, 2_000_000, integer=True)
    _number(agent.max_observation_chars, "agent.max_observation_chars", 1, 250_000, integer=True)
    if type(tools.wikipedia) is not bool:
        raise ValueError("tools.wikipedia must be true or false.")
    _string(tools.workspace, "tools.workspace")
    if agent.paper and not tools.wikipedia:
        raise ValueError("Paper examples require the Wikipedia tools.")
    names = {"think", "finish"}
    if tools.wikipedia:
        names.update({"search", "lookup"})
    if tools.workspace:
        names.update({"read", "list"})
    for item in custom:
        if not isinstance(item, dict) or set(item) != {"name", "description", "callable"}:
            raise ValueError("Each custom tool requires name, description and callable.")
        tool = CustomTool(**item)
        _string(tool.name, "tool.name", 64, empty=False)
        _string(tool.description, "tool.description", 2000, empty=False)
        _string(tool.callable, "tool.callable", 512, empty=False)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tool.name) or tool.name.lower() in names:
            raise ValueError("Tool names must be unique identifiers and must not overlap built-in tools.")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", tool.callable, re.ASCII):
            raise ValueError("Tool callable must use module:function syntax, such as my_tools:lookup.")
        names.add(tool.name.lower())
        tools.custom.append(tool)
    return Config(model, agent, tools, Path(path).absolute())


def _read(path: Path) -> bytes:
    with path.open("rb") as reader:
        raw = reader.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Configuration exceeds 64 KB.")
    return raw


def revision(path: str | Path) -> str:
    try:
        return hashlib.sha256(_read(Path(path))).hexdigest()
    except FileNotFoundError:
        return "missing"


def load_config(path: str | Path = "nreact.toml", *, missing_ok: bool = False) -> Config:
    target = Path(path).absolute()
    try:
        raw = _read(target)
    except FileNotFoundError:
        if not missing_ok:
            raise
        return parse_config({}, target)
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        # Parser error messages can quote secret-bearing source text.
        raise ValueError("Invalid TOML configuration. Check its syntax and encoding.") from None
    return parse_config(data, target)


def dumps_config(config: Config) -> str:
    """Serialize the fixed schema as TOML, using JSON-compatible string escapes."""
    data = parse_config(config.to_dict(), config.path, use_environment=False).to_dict()
    lines = ["# nreact local configuration. Keep this file out of version control."]
    for section in ("model", "agent", "tools"):
        lines.extend(["", f"[{section}]"])
        for key, value in data[section].items():
            if key == "custom" or (key == "api_key" and not value):
                continue
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    for tool in data["tools"]["custom"]:
        lines.extend(["", "[[tools.custom]]"])
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in tool.items())
    text = "\n".join(lines) + "\n"
    if len(text.encode()) > MAX_CONFIG_BYTES:
        raise ValueError("Configuration exceeds 64 KB.")
    return text


def save_config(config: Config, *, overwrite: bool = False) -> None:
    """Write a complete config; overwrites use atomic replacement and mode 0600."""
    text = dumps_config(config)
    path = config.path
    if path.is_symlink():
        raise ValueError("Saving through a configuration symlink is unsupported.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not overwrite:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as writer:
            writer.write(text)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".nreact-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as writer:
            writer.write(text)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_tool(reference: str, directory: Path):
    module_name, function_name = reference.split(":")
    local_file = directory.joinpath(*module_name.split(".")).with_suffix(".py")
    if local_file.is_file():
        # Local single-file modules load relative to the configuration, independently
        # of the shell's current directory. Their top-level code runs only at run time.
        identity = "_nreact_tool_" + hashlib.sha256(str(local_file).encode()).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(identity, local_file)
        if spec is None or spec.loader is None:
            raise ValueError("Could not load the configured tool module.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[identity] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(identity, None)
            raise
    else:
        module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    if not callable(function):
        raise ValueError("Configured tool handler is not callable.")
    return function


class ConfiguredEnvironment:
    def __init__(self, config: Config):
        self.wiki = WikiEnvironment() if config.tools.wikipedia else None
        tools = []
        if config.tools.workspace:
            tools.extend(workspace_tools(config.path.parent / config.tools.workspace))
        for item in config.tools.custom:
            tools.append(Tool(item.name, item.description, _load_tool(item.callable, config.path.parent)))
        self.tools = ToolEnvironment(tools)

    @property
    def instructions(self) -> str:
        return "\n".join(filter(None, [self.wiki.instructions if self.wiki else "", self.tools.instructions]))

    def reset(self) -> None:
        if self.wiki:
            self.wiki.reset()
        self.tools.reset()

    def step(self, name: str, argument: str) -> Observation:
        if self.wiki and name in {"search", "lookup"}:
            return self.wiki.step(name, argument)
        return self.tools.step(name, argument)


def build_agent(config: Config) -> Agent:
    config = parse_config(config.to_dict(), config.path, use_environment=False)
    if not config.model.name.strip():
        raise ValueError("Set model.name in the configuration or NREACT_MODEL in the environment.")
    if config.model.auth == "chatgpt":
        auth_file = config.path.parent / Path(config.model.auth_file).expanduser() if config.model.auth_file else None
        model = ChatGPTModel(config.model.name, auth_file=auth_file, timeout=config.model.timeout,
                             stop_locally=config.model.send_stop)
    else:
        model = ChatModel(config.model.name, base_url=config.model.base_url, api_key=config.api_key(),
                          timeout=config.model.timeout, max_tokens=config.model.max_tokens,
                          temperature=config.model.temperature, send_stop=config.model.send_stop)
    return Agent(model, ConfiguredEnvironment(config), mode=config.agent.mode,
                 examples=paper_examples(config.agent.paper) if config.agent.paper else "",
                 max_steps=config.agent.max_steps, max_context_chars=config.agent.max_context_chars,
                 max_observation_chars=config.agent.max_observation_chars)
