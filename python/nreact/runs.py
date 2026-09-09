"""Local workbench run history and cooperative run lifecycle."""

import copy
import heapq
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ._run_store import load_record, validate_identity, write_record
from .config import build_agent, parse_config
from .control import RunControl

MAX_UNSAVED_RUNS = 50


class RunHistory:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.active = None
        self.control = None
        # Only live runs and records whose final save failed stay in memory.
        self.records = {}

    def _read(self, identity):
        validate_identity(identity)
        if identity in self.records:
            return self.records[identity]
        return load_record(self.directory, identity)

    def snapshot(self, identity):
        with self.lock:
            record = copy.deepcopy(self._read(identity))
            if self.active == identity:
                record["status"] = self.control.state
            return record

    def list(self):
        with self.lock:
            def summaries():
                for path in self.directory.glob("*.json"):
                    if path.stem in self.records:
                        continue
                    try:
                        yield self._summary(self._read(path.stem))
                    except (OSError, ValueError):
                        continue
                for record in self.records.values():
                    yield self._summary(record)

            # Retain only fifty summaries while scanning; full traces are released.
            items = heapq.nlargest(50, summaries(), key=lambda item: (
                datetime.fromisoformat(item["started_at"]), item["id"]))
            return {"runs": items, "active": self.active}

    def _summary(self, record):
        item = {key: record.get(key) for key in (
            "id", "task", "started_at", "status", "demo", "steps", "model", "elapsed_seconds")}
        if item["id"] == self.active:
            item["status"] = self.control.state
        return item

    def _persist(self, record):
        write_record(self.directory, {key: value for key, value in record.items() if key != "storage_error"})

    def _finish(self, record):
        """Release lifecycle state even when serialization or storage fails."""
        with self.lock:
            try:
                self._persist(record)
            except (OSError, ValueError, TypeError, RecursionError):
                record["storage_error"] = "Could not save this run to disk. Export it before closing the server."
            else:
                self.records.pop(record["id"], None)
            finally:
                self.active = None
                self.control = None

    def start(self, config, task, *, demo=False, single_step=False):
        with self.lock:
            if self.active:
                raise ValueError("Finish or stop the active run before starting another.")
            if len(self.records) >= MAX_UNSAVED_RUNS:
                raise ValueError("Too many unsaved runs. Export them and repair storage before restarting the server.")
            self.directory.mkdir(parents=True, exist_ok=True)
            identity = uuid.uuid4().hex
            if any((self.directory / f"{identity}.{suffix}").exists()
                   or (self.directory / f"{identity}.{suffix}").is_symlink() for suffix in ("json", "jsonl")):
                raise ValueError("Run identifier already exists. Try starting again.")
            if demo:
                config = parse_config({"model": {"name": "Scripted demo"}, "agent": {"max_steps": 7},
                                       "tools": {"wikipedia": True}}, config.path, use_environment=False)
                task = "Where was the designer of the Lumen telescope born?"
            record = {"id": identity, "task": task, "demo": demo,
                      "model": config.model.name, "config": config.public_dict(),
                      "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
                      "steps": 0, "events": [], "result": None, "error": None, "elapsed_seconds": 0}
            # A recoverable manifest exists before any model or tool can run.
            try:
                self._persist(record)
            except (OSError, ValueError, TypeError, RecursionError):
                raise ValueError("Could not create the run record. Check the storage path and permissions.") from None
            self.records[identity] = record
            controller = RunControl(paused=single_step)
            if single_step:
                controller.command("step")
            self.active, self.control = identity, controller
            started = time.monotonic()

            def event_callback(event):
                with self.lock:
                    item = {**asdict(event), "index": len(record["events"]),
                            "elapsed_seconds": round(time.monotonic() - started, 4)}
                    record["events"].append(item)
                    record["steps"] = event.step
                    record["elapsed_seconds"] = item["elapsed_seconds"]

            def execute():
                try:
                    if demo:
                        from .cli import demo_agent
                        agent = demo_agent()
                    else:
                        agent = build_agent(config)
                    result = agent.run(task, on_event=event_callback, control=controller,
                                       trace_path=self.directory / f"{identity}.jsonl")
                    with self.lock:
                        record["result"] = result.to_dict()
                        record["status"] = result.status
                        record["steps"] = result.steps
                        record["error"] = result.error
                except BaseException as exc:
                    with self.lock:
                        record["result"] = None
                        record["status"] = "error"
                        record["error"] = f"Run failed ({type(exc).__name__}). Check the model and tool configuration."
                finally:
                    with self.lock:
                        record["elapsed_seconds"] = time.monotonic() - started
                        self._finish(record)

            try:
                threading.Thread(target=execute, name="nreact-run", daemon=True).start()
            except Exception:
                record["status"] = "error"
                record["error"] = "Could not start the run worker. No model or tool was called."
                self._finish(record)
                raise ValueError(record["error"]) from None
            return {"id": identity}

    def command(self, identity, action):
        with self.lock:
            if identity != self.active or self.control is None:
                raise ValueError("This run has finished. Use the step navigator to inspect its history.")
            self.control.command(action)
            return {"status": self.control.state}
