"""Local run history, bounded FIFO queue and cooperative execution lifecycle."""

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
from .control import RunCancelled, RunControl

MAX_UNSAVED_RUNS = 50
MAX_QUEUED_RUNS = 32


class RunHistory:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.active = None
        self.control = None
        # Only live, queued and unsuccessfully saved records stay in memory.
        self.records = {}
        # Credentials stay in these private execution inputs, never in manifests.
        self._pending = {}
        self.queue_paused = False
        self._revision = 0

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

    def state(self):
        with self.lock:
            return {"active": self.active, "queued": list(self._pending),
                    "queue_paused": self.queue_paused, "revision": self._revision}

    def updates(self, identity, offset=0):
        """Copy only unseen events and current lifecycle metadata for browser polling."""
        with self.lock:
            record = self._read(identity)
            if type(offset) is not int or not 0 <= offset <= len(record["events"]):
                raise ValueError("Event offset is outside the recorded history.")
            stop, characters = offset, 0
            for event in record["events"][offset:offset + 128]:
                size = len(event["text"])
                if stop > offset and characters + size > 128_000:
                    break
                stop += 1
                characters += size
            data = {key: record.get(key) for key in (
                "id", "status", "steps", "elapsed_seconds", "error", "storage_error")}
            if identity == self.active:
                data["status"] = self.control.state
            data.update(offset=offset, next_offset=stop, event_count=len(record["events"]),
                        events=copy.deepcopy(record["events"][offset:stop]),
                        has_more=stop < len(record["events"]), result=None)
            if not data["has_more"] and record.get("result") is not None:
                data["result"] = copy.deepcopy({key: value for key, value in record["result"].items()
                                                if key != "events"})
            return data

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

            items = heapq.nlargest(50, summaries(), key=lambda item: (
                datetime.fromisoformat(item["started_at"]), item["id"]))
            return {"runs": items, **self.state()}

    def _summary(self, record):
        item = {key: record.get(key) for key in (
            "id", "task", "started_at", "status", "demo", "steps", "model", "elapsed_seconds")}
        if item["id"] == self.active:
            item["status"] = self.control.state
        return item

    def _persist(self, record):
        write_record(self.directory, {key: value for key, value in record.items() if key != "storage_error"})

    def _save_finished(self, record):
        try:
            self._persist(record)
        except (OSError, ValueError, TypeError, RecursionError):
            record["storage_error"] = "Could not save this run to disk. Export it before closing the server."
            self.queue_paused = True
        else:
            self.records.pop(record["id"], None)

    def _finish(self, record):
        """Release lifecycle state even when serialization or storage fails."""
        with self.lock:
            try:
                self._save_finished(record)
                if record["status"] in {"error", "model_error", "environment_error"}:
                    self.queue_paused = True
            finally:
                self.active = None
                self.control = None
                self._revision += 1
            self._start_next()

    def _start_next(self):
        if self.active or self.queue_paused or not self._pending:
            return
        identity = next(iter(self._pending))
        config, submitted = self._pending.pop(identity)
        record = self.records[identity]
        record["queued_seconds"] = max(0, time.monotonic() - submitted)
        try:
            self._launch(config, record, single_step=False)
        except ValueError:
            # _launch retains a sanitized error record and pauses the queue.
            pass

    def start(self, config, task, *, demo=False, single_step=False, queue=False):
        with self.lock:
            if type(queue) is not bool:
                raise ValueError("Queue option must be a boolean.")
            if queue and single_step:
                raise ValueError("Queued runs execute continuously. Use Step for an immediate run.")
            if not queue and (self.active or self._pending):
                raise ValueError("Finish or stop the active run and pending queue before starting another. Use Queue task to add work.")
            if queue and len(self._pending) >= MAX_QUEUED_RUNS:
                raise ValueError(f"Queue is full ({MAX_QUEUED_RUNS} pending runs). Wait or remove a queued task.")
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
            else:
                config = copy.deepcopy(config)
            record = {"id": identity, "task": task, "demo": demo,
                      "model": config.model.name, "config": config.public_dict(),
                      "started_at": datetime.now(timezone.utc).isoformat(),
                      "status": "queued" if queue else "running",
                      "steps": 0, "events": [], "result": None, "error": None, "elapsed_seconds": 0}
            # A recoverable manifest exists before construction or queue admission.
            try:
                self._persist(record)
            except (OSError, ValueError, TypeError, RecursionError):
                raise ValueError("Could not create the run record. Check the storage path and permissions.") from None
            self.records[identity] = record
            self._revision += 1
            if queue:
                self._pending[identity] = (config, time.monotonic())
                self._start_next()
            else:
                self._launch(config, record, single_step=single_step)
            return {"id": identity}

    def _launch(self, config, record, *, single_step):
        identity, task, demo = record["id"], record["task"], record["demo"]
        record["status"] = "running"
        record["execution_started_at"] = datetime.now(timezone.utc).isoformat()
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
                controller.check_cancelled()
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
            except RunCancelled:
                with self.lock:
                    record["status"] = "cancelled"
                    record["error"] = None
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

    def command(self, identity, action):
        with self.lock:
            validate_identity(identity)
            if identity in self._pending:
                if action != "cancel":
                    raise ValueError("This task is queued. Resume the queue or remove this task.")
                self._pending.pop(identity)
                record = self.records[identity]
                record["status"] = "cancelled"
                self._save_finished(record)
                self._revision += 1
                return {"status": "cancelled"}
            if identity != self.active or self.control is None:
                raise ValueError("This run has finished. Use the step navigator to inspect its history.")
            self.control.command(action)
            if action == "cancel":
                self.queue_paused = True
            self._revision += 1
            return {"status": self.control.state}

    def queue_command(self, action):
        with self.lock:
            if action == "pause":
                self.queue_paused = True
            elif action == "resume":
                self.queue_paused = False
                self._start_next()
            else:
                raise ValueError("Choose pause or resume for the queue.")
            self._revision += 1
            return self.state()
