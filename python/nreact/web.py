"""Loopback-only HTTP API and packaged Vue UI for local agent configuration."""

import copy
import hmac
import json
import secrets
import threading
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .config import build_agent, load_config, parse_config, revision, save_config


class ConflictError(ValueError):
    pass


class LocalApp:
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.run = {"status": "idle", "events": [], "result": None}

    def _load(self):
        before = revision(self.path)
        config = load_config(self.path, missing_ok=True)
        if before != revision(self.path):
            raise ConflictError("Configuration changed on disk. Reload and try again.")
        return config, before

    def snapshot(self) -> dict:
        with self.lock:
            config, version = self._load()
            return {"config": config.public_dict(), "path": str(self.path), "revision": version,
                    "key_status": "saved" if config.model.api_key else "environment" if config.api_key() else "empty"}

    def save(self, payload: dict) -> dict:
        with self.lock:
            current, version = self._load()
            if payload.get("revision") != version:
                raise ConflictError("Configuration changed on disk. Reload before saving.")
            data = copy.deepcopy(payload.get("config"))
            if not isinstance(data, dict) or not isinstance(data.get("model"), dict):
                raise ValueError("A model configuration is required.")
            action = payload.get("api_key_action", "keep")
            if action == "keep":
                data["model"]["api_key"] = current.model.api_key
            elif action == "clear":
                data["model"]["api_key"] = ""
            elif action == "replace":
                if not data["model"].get("api_key"):
                    raise ValueError("Enter a new API key before replacing the saved key.")
            else:
                raise ValueError("Unknown API key operation.")
            config = parse_config(data, self.path, use_environment=False)
            if revision(self.path) != version:
                raise ConflictError("Configuration changed on disk. Reload before saving.")
            save_config(config, overwrite=version != "missing")
            return self.snapshot()

    def start(self, payload: dict) -> dict:
        task = payload.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 16_000:
            raise ValueError("Enter a task of 1–16000 characters.")
        with self.lock:
            if self.run["status"] == "running":
                raise ConflictError("An agent is already running.")
            config, version = self._load()
            if version == "missing" or payload.get("revision") != version:
                raise ConflictError("Save or reload the configuration before running.")
            if not config.model.name.strip():
                raise ValueError("Set a model name before running.")
            identity = uuid.uuid4().hex
            self.run = {"id": identity, "status": "running", "task": task,
                        "events": [], "result": None, "error": None}

            def on_event(event):
                with self.lock:
                    self.run["events"].append(asdict(event))

            def execute():
                try:
                    result = build_agent(config).run(task, on_event=on_event)
                    with self.lock:
                        self.run["result"] = result.to_dict()
                        self.run["status"] = "completed"
                except BaseException as exc:
                    # A local custom module can raise arbitrary exception messages.
                    with self.lock:
                        self.run["status"] = "error"
                        self.run["error"] = f"Run failed ({type(exc).__name__}). Check the model and tool configuration."

            threading.Thread(target=execute, daemon=True, name="nreact-agent").start()
            return {"id": identity, "status": "running"}

    def run_snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.run)


def make_server(path: str | Path = "nreact.toml", *, port: int = 8765) -> ThreadingHTTPServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")
    app = LocalApp(path)
    assets = files("nreact").joinpath("web_static")
    if not assets.joinpath("index.html").is_file():
        raise ValueError("Web assets are missing. Build the web directory with pnpm build.")

    class Handler(BaseHTTPRequestHandler):
        server_version = "nreact"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                             "frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode())

        def _guard(self, *, api: bool = False) -> bool:
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            if host not in hosts:
                self._json(403, {"error": "Local host required."})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                self._json(403, {"error": "Same-origin request required."})
                return False
            if api and not hmac.compare_digest(self.headers.get("X-Nreact-Token", "").encode(), app.token.encode()):
                self._json(403, {"error": "Reload the local interface to start a session."})
                return False
            return True

        def do_GET(self):
            route = urlsplit(self.path).path
            if not self._guard(api=route.startswith("/api/")):
                return
            try:
                if route == "/api/config":
                    self._json(200, app.snapshot())
                elif route == "/api/run":
                    self._json(200, app.run_snapshot())
                elif route == "/":
                    html = assets.joinpath("index.html").read_text(encoding="utf-8")
                    self._send(200, html.replace("__NREACT_TOKEN__", app.token).encode(), "text/html")
                elif route == "/favicon.svg":
                    self._send(200, assets.joinpath("favicon.svg").read_bytes(), "image/svg+xml")
                elif route.startswith("/assets/"):
                    name = route.removeprefix("/assets/")
                    if "/" in name or "\\" in name or not name or ".." in name:
                        self._json(404, {"error": "Not found."})
                        return
                    suffix = Path(name).suffix
                    content_type = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(suffix)
                    if not content_type:
                        self._json(404, {"error": "Not found."})
                        return
                    self._send(200, assets.joinpath("assets", name).read_bytes(), content_type)
                else:
                    self._json(404, {"error": "Not found."})
            except ConflictError as exc:
                self._json(409, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except OSError:
                self._json(404, {"error": "File unavailable. Check the configuration path."})

        def do_POST(self):
            if not self._guard(api=True):
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    self._json(415, {"error": "Send application/json."})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65_536 or self.headers.get("Transfer-Encoding"):
                    self._json(413, {"error": "Request must contain at most 64 KB of JSON."})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    self._json(400, {"error": "Invalid JSON request."})
                    return
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object.")
                route = urlsplit(self.path).path
                if route == "/api/config":
                    self._json(200, app.save(payload))
                elif route == "/api/run":
                    self._json(202, app.start(payload))
                else:
                    self._json(404, {"error": "Not found."})
            except ConflictError as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else "Invalid configuration field types."
                self._json(400, {"error": message})
            except OSError:
                self._json(400, {"error": "Could not write configuration. Check the file path and permissions."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(path: str | Path = "nreact.toml", *, port: int = 8765, open_browser: bool = True) -> None:
    with make_server(path, port=port) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"nreact UI: {url}\nConfiguration: {Path(path).absolute()}", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\nnreact UI stopped.")
