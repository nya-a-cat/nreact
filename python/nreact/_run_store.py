"""Bounded, atomic run records and read-only recovery of execution journals."""

import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

MAX_RECORD_BYTES = 24_000_000
MAX_TRACE_BYTES = 3 * MAX_RECORD_BYTES
LIVE_STATUSES = frozenset({"running", "paused", "pausing", "cancelling"})


def validate_identity(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid run identifier.")


def _number(value, *, nonnegative=True):
    return (type(value) in (int, float)
            and (type(value) is int or math.isfinite(value))
            and (not nonnegative or value >= 0))


def _integer(value):
    return type(value) is int and value >= 0


def _optional_text(value):
    return value is None or isinstance(value, str)


def _event(value):
    if (not isinstance(value, dict) or not isinstance(value.get("kind"), str)
            or not isinstance(value.get("text"), str) or not _integer(value.get("step"))
            or not _optional_text(value.get("tool"))):
        raise ValueError("Invalid run event.")
    return {key: value.get(key) for key in ("kind", "step", "text", "tool")}


def _result(value):
    if (not isinstance(value, dict) or not isinstance(value.get("status"), str)
            or value["status"] in LIVE_STATUSES or not value["status"]
            or not _integer(value.get("steps")) or not _integer(value.get("model_calls"))
            or not _number(value.get("elapsed_seconds"))
            or not _optional_text(value.get("answer")) or not _optional_text(value.get("error"))
            or not isinstance(value.get("usage"), dict)
            or any(not _integer(count) for count in value["usage"].values())
            or not isinstance(value.get("events"), list)
            or (value.get("reward") is not None and not _number(value["reward"], nonnegative=False))):
        raise ValueError("Invalid run result.")
    for item in value["events"]:
        _event(item)
    return value


def _validate_record(record, identity):
    if (not isinstance(record, dict) or record.get("id") != identity
            or not isinstance(record.get("task"), str)
            or not isinstance(record.get("model"), str)
            or not isinstance(record.get("started_at"), str)
            or not isinstance(record.get("status"), str) or not record["status"]
            or type(record.get("demo")) is not bool
            or not _integer(record.get("steps"))
            or not _number(record.get("elapsed_seconds"))
            or not isinstance(record.get("events"), list)
            or not isinstance(record.get("config"), dict)
            or not _optional_text(record.get("error"))):
        raise ValueError("Invalid run record.")
    try:
        timestamp = datetime.fromisoformat(record["started_at"])
        if timestamp.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ValueError("Invalid run timestamp.") from None
    for index, item in enumerate(record["events"]):
        _event(item)
        if (type(item.get("index")) is not int or item["index"] != index
                or not _number(item.get("elapsed_seconds"))):
            raise ValueError("Invalid run event metadata.")
    if record.get("result") is not None:
        if _result(record["result"])["status"] != record["status"]:
            raise ValueError("Inconsistent run result.")
    return record


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Invalid JSON number.")
    return value


def _invalid_constant(text):
    raise ValueError("Invalid JSON number.")


def _decode(data):
    try:
        return json.loads(data, parse_float=_finite_float, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid run JSON.") from None


def write_record(directory: Path, record: dict) -> None:
    """Publish a complete JSON file; a failed write leaves its predecessor intact."""
    identity = record.get("id")
    validate_identity(identity)
    _validate_record(record, identity)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{identity}-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as writer:
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(record):
                data = chunk.encode("utf-8")
                size += len(data)
                if size > MAX_RECORD_BYTES:
                    raise ValueError("Run record is too large.")
                writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, directory / f"{identity}.json")
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _recover(directory, record):
    """Replay complete journal entries without importing a model or executing tools."""
    record["status"] = "interrupted"
    record["result"] = None
    record["error"] = "This run has no live worker in this server. Recorded events were recovered; tools were not restarted."
    path = directory / f"{record['id']}.jsonl"
    try:
        if path.is_symlink():
            raise ValueError("Linked run traces are not supported.")
        with path.open("rb") as reader:
            remaining = MAX_TRACE_BYTES
            started = False
            events = []
            elapsed = 0.0
            while remaining:
                line = reader.readline(min(remaining, MAX_RECORD_BYTES) + 1)
                if not line:
                    break
                if len(line) > remaining or len(line) > MAX_RECORD_BYTES:
                    raise ValueError("Run trace is too large.")
                remaining -= len(line)
                # A torn final line is not a committed journal entry.
                if not line.endswith(b"\n"):
                    raise ValueError("Incomplete run trace.")
                item = _decode(line)
                if not isinstance(item, dict):
                    raise ValueError("Invalid run trace entry.")
                kind = item.get("type")
                if not started:
                    if (kind != "start" or item.get("schema_version") != 1
                            or item.get("task") != record["task"]):
                        raise ValueError("Run trace does not match its record.")
                    started = True
                elif kind == "event":
                    event = _event(item)
                    seconds = item.get("elapsed_seconds", elapsed)
                    if not _number(seconds) or seconds < elapsed:
                        raise ValueError("Invalid run trace timestamp.")
                    elapsed = seconds
                    events.append({**event, "index": len(events), "elapsed_seconds": elapsed})
                    record["events"] = events
                    record["steps"] = event["step"]
                    record["elapsed_seconds"] = elapsed
                elif kind == "result":
                    result = _result({key: value for key, value in item.items() if key != "type"})
                    if [_event(event) for event in result["events"]] != [_event(event) for event in events]:
                        raise ValueError("Run result does not match its journal events.")
                    record.update(result=result, events=events, status=result["status"],
                                  steps=result["steps"], elapsed_seconds=result["elapsed_seconds"],
                                  error=result.get("error"))
                    return record
                elif kind != "generation":
                    raise ValueError("Unexpected run trace entry.")
            if not remaining and reader.read(1):
                raise ValueError("Run trace is too large.")
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        record["error"] += " Recovery stopped at the last readable journal entry."
    return record


def load_record(directory: Path, identity: str) -> dict:
    validate_identity(identity)
    path = directory / f"{identity}.json"
    if path.is_symlink():
        raise ValueError("Linked run records are not supported.")
    with path.open("rb") as reader:
        data = reader.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Run record is too large.")
    record = _validate_record(_decode(data), identity)
    return _recover(directory, record) if record["status"] in LIVE_STATUSES else record
