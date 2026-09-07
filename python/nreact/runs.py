"""Local workbench run history and cooperative run lifecycle."""

import copy
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import build_agent, parse_config
from .control import RunControl


class RunHistory:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.active = None
        self.control = None
        self.records = {}

    def _read(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid run identifier.")
        if identity in self.records:
            return self.records[identity]
        path = self.directory / f"{identity}.json"
        if path.stat().st_size > 24_000_000:
            raise ValueError("Run record is too large.")
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self, identity):
        with self.lock:
            record = copy.deepcopy(self._read(identity))
            if self.active == identity:
                record["status"] = self.control.state
            return record

    def list(self):
        with self.lock:
            paths = sorted(self.directory.glob("*.json"), key=lambda p: p.name, reverse=True)
            records = {}
            for path in paths:
                try:
                    record = self._read(path.stem)
                    records[record["id"]] = record
                except (OSError, ValueError, KeyError):
                    continue
            records.update(self.records)
            items = []
            for record in records.values():
                item = {k: record.get(k) for k in ("id", "task", "started_at", "status", "demo", "steps", "model", "elapsed_seconds")}
                if item["id"] == self.active:
                    item["status"] = self.control.state
                items.append(item)
            return {"runs": sorted(items, key=lambda r: r["started_at"], reverse=True)[:50], "active": self.active}

    def _persist(self, record):
        path = self.directory / f"{record['id']}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            json.dump(record, writer, ensure_ascii=False)

    def start(self, config, task, *, demo=False, single_step=False):
        with self.lock:
            if self.active:
                raise ValueError("Finish or stop the active run before starting another.")
            self.directory.mkdir(parents=True, exist_ok=True)
            identity = uuid.uuid4().hex
            if demo:
                config = parse_config({"model": {"name": "Scripted demo"}, "agent": {"max_steps": 7},
                                       "tools": {"wikipedia": True}}, config.path, use_environment=False)
                task = "Where was the designer of the Lumen telescope born?"
            record = {"id": identity, "task": task, "demo": demo,
                      "model": config.model.name, "config": config.public_dict(),
                      "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
                      "steps": 0, "events": [], "result": None, "error": None, "elapsed_seconds": 0}
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
                        record["elapsed_seconds"] = time.monotonic() - started
                        record["error"] = result.error
                except BaseException as exc:
                    with self.lock:
                        record["status"] = "error"
                        record["error"] = f"Run failed ({type(exc).__name__}). Check the model and tool configuration."
                finally:
                    with self.lock:
                        try:
                            self._persist(record)
                        except OSError:
                            record["storage_error"] = "Could not save this run to disk. Export it before closing the server."
                        self.active = None

            threading.Thread(target=execute, name="nreact-run", daemon=True).start()
            return {"id": identity}

    def command(self, identity, action):
        with self.lock:
            if identity != self.active or self.control is None:
                raise ValueError("This run has finished. Use the step navigator to inspect its history.")
            self.control.command(action)
            return {"status": self.control.state}
