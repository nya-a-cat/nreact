"""Portable ReAct workflow documents and revision-checked local storage."""

import copy
import hashlib
import heapq
import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import parse_config
from .graph import GRAPH_SCHEMA, REACT_CONNECTIONS

MAX_WORKFLOW_BYTES = 60_000
MAX_RECORD_BYTES = 65_536
WORKFLOW_FORMAT = "nreact.workflow"


class WorkflowConflict(ValueError):
    """A stored workflow changed after the editor loaded it."""


def validate_connections(value, *, complete=False):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Graph connections must be a list of at most 32 links.")
    actual = set()
    keys = ("source", "sourceHandle", "target", "targetHandle")
    for edge in value:
        if not isinstance(edge, dict) or set(edge) != set(keys):
            raise ValueError("Each connection requires source, sourceHandle, target and targetHandle.")
        connection = tuple(edge[key] for key in keys)
        if not all(isinstance(item, str) for item in connection) or connection not in REACT_CONNECTIONS:
            raise ValueError("Connection ports are incompatible with the ReAct components.")
        if connection in actual:
            raise ValueError("Duplicate graph connection.")
        actual.add(connection)
    if complete and actual != REACT_CONNECTIONS:
        missing = sorted(f"{target}.{handle}" for _, _, target, handle in REACT_CONNECTIONS - actual)
        raise ValueError(f"Connect {', '.join(missing)} before running.")
    return copy.deepcopy(value)


def _coordinate(value, limit=1_000_000):
    return type(value) in (int, float) and -limit <= value <= limit and math.isfinite(value)


def validate_graph(value):
    """Validate a graph without importing tools or invoking Vue Flow."""
    if (not isinstance(value, dict) or set(value) - {"version", "positions", "connections", "viewport"}
            or type(value.get("version")) is not int or value["version"] != GRAPH_SCHEMA["version"]):
        raise ValueError("Unsupported graph document.")
    positions = value.get("positions")
    node_ids = {node["id"] for node in GRAPH_SCHEMA["nodes"]}
    if not isinstance(positions, dict) or set(positions) - node_ids:
        raise ValueError("Graph positions must reference known nodes.")
    for position in positions.values():
        if (not isinstance(position, dict) or set(position) != {"x", "y"}
                or not all(_coordinate(position[key]) for key in ("x", "y"))):
            raise ValueError("Graph coordinates must be finite numbers between -1000000 and 1000000.")
    graph = {"version": GRAPH_SCHEMA["version"], "positions": copy.deepcopy(positions),
             "connections": validate_connections(value.get("connections"))}
    if "viewport" in value:
        viewport = value["viewport"]
        if (not isinstance(viewport, dict) or set(viewport) != {"x", "y", "zoom"}
                or not all(_coordinate(viewport[key]) for key in ("x", "y"))
                or type(viewport["zoom"]) not in (int, float) or not .3 <= viewport["zoom"] <= 1.6):
            raise ValueError("Invalid graph viewport.")
        graph["viewport"] = copy.deepcopy(viewport)
    return graph


def _encode(value, limit):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Workflow must contain valid JSON and Unicode.") from None
    if len(encoded) > limit:
        raise ValueError(f"Workflow exceeds the {limit}-byte size limit.")
    return encoded


def validate_workflow(value, path="nreact.toml", *, imported=False):
    """Return a normalized, credential-free document. Never execute its contents."""
    _encode(value, MAX_WORKFLOW_BYTES)
    if (not isinstance(value, dict) or set(value) != {"format", "version", "name", "config", "task", "graph"}
            or value["format"] != WORKFLOW_FORMAT or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Expected an nreact.workflow version 1 document.")
    name, task = value["name"], value["task"]
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError("Workflow name must be a single line of 1-120 characters.")
    if not isinstance(task, str) or len(task) > 16_000:
        raise ValueError("Workflow task must contain at most 16000 characters.")
    data = value["config"]
    if isinstance(data, dict) and isinstance(data.get("model"), dict) and "api_key" in data["model"]:
        raise ValueError("Remove model.api_key before importing or saving a workflow.")
    config = parse_config(data, path, use_environment=False).public_dict()
    if imported:
        # Files from other machines cannot select local credential sources.
        config["model"].update(api_key_env="", auth_file="", auth="api_key")
    document = {"format": WORKFLOW_FORMAT, "version": 1, "name": name.strip(),
                "config": config, "task": task, "graph": validate_graph(value["graph"])}
    _encode(document, MAX_WORKFLOW_BYTES)
    return document


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid workflow identifier.")
    return value


class WorkflowStore:
    def __init__(self, directory: Path, config_path: Path):
        self.directory = Path(directory)
        self.config_path = Path(config_path)
        self.lock = threading.RLock()

    def _load(self, identity):
        path = self.directory / f"{_identity(identity)}.json"
        if path.is_symlink():
            raise ValueError("Linked workflow files are not supported.")
        with path.open("rb") as reader:
            data = reader.read(MAX_RECORD_BYTES + 1)
        if len(data) > MAX_RECORD_BYTES:
            raise ValueError("Workflow record is too large.")
        try:
            record = json.loads(data)
            if (not isinstance(record, dict) or set(record) != {"id", "updated_at", "workflow"}
                    or record["id"] != identity or not isinstance(record["updated_at"], str)
                    or datetime.fromisoformat(record["updated_at"]).utcoffset() is None):
                raise ValueError()
            record["workflow"] = validate_workflow(record["workflow"], self.config_path)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError("Invalid stored workflow.") from None
        return {**record, "revision": hashlib.sha256(data).hexdigest()}

    def get(self, identity):
        with self.lock:
            return self._load(identity)

    def list(self):
        with self.lock:
            count = 0

            def summaries():
                nonlocal count
                for path in self.directory.glob("*.json"):
                    try:
                        record = self._load(path.stem)
                    except (OSError, ValueError):
                        continue
                    count += 1
                    yield {"id": record["id"], "revision": record["revision"],
                           "updated_at": record["updated_at"], "name": record["workflow"]["name"],
                           "model": record["workflow"]["config"]["model"]["name"]}

            items = heapq.nlargest(200, summaries(), key=lambda item: (
                datetime.fromisoformat(item["updated_at"]), item["id"]))
            return {"workflows": items, "total": count, "truncated": count > len(items)}

    def save(self, payload):
        document = validate_workflow(payload.get("workflow"), self.config_path)
        identity, expected = payload.get("id"), payload.get("revision")
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if identity is None:
                if expected is not None:
                    raise ValueError("A new workflow cannot have a revision.")
                identity = uuid.uuid4().hex
                path = self.directory / f"{identity}.json"
                if path.exists() or path.is_symlink():
                    raise WorkflowConflict("Workflow identifier already exists. Try again.")
            else:
                try:
                    current = self._load(identity)
                except FileNotFoundError:
                    raise WorkflowConflict("Workflow was deleted. Save a new copy instead.") from None
                if current["revision"] != expected:
                    raise WorkflowConflict("Workflow changed on disk. Reload it before saving.")
            record = {"id": identity, "updated_at": datetime.now(timezone.utc).isoformat(), "workflow": document}
            encoded = _encode(record, MAX_RECORD_BYTES)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{identity}-", dir=self.directory)
            try:
                with os.fdopen(descriptor, "wb") as writer:
                    writer.write(encoded)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary, self.directory / f"{identity}.json")
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            return {**record, "revision": hashlib.sha256(encoded).hexdigest()}

    def delete(self, payload):
        with self.lock:
            identity = _identity(payload.get("id"))
            if self._load(identity)["revision"] != payload.get("revision"):
                raise WorkflowConflict("Workflow changed on disk. Reload it before deleting.")
            (self.directory / f"{identity}.json").unlink()
            return {"deleted": identity}
