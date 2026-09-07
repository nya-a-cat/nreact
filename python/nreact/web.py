"""Loopback-only HTTP API and packaged Vue UI for local agent configuration."""

import copy
import hmac
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import dumps_config, load_config, parse_config, revision, save_config, tomllib
from .runs import RunHistory
from .graph import GRAPH_SCHEMA, REACT_CONNECTIONS


class ConflictError(ValueError):
    pass


def validate_connections(value, *, complete=False):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Graph connections must be a list of at most 32 links.")
    actual = set()
    for edge in value:
        if not isinstance(edge, dict) or set(edge) != {"source", "sourceHandle", "target", "targetHandle"}:
            raise ValueError("Each connection requires source, sourceHandle, target and targetHandle.")
        connection = tuple(edge[key] for key in ("source", "sourceHandle", "target", "targetHandle"))
        if not all(isinstance(item, str) for item in connection) or connection not in REACT_CONNECTIONS:
            raise ValueError("Connection ports are incompatible with the ReAct components.")
        if connection in actual:
            raise ValueError("Duplicate graph connection.")
        actual.add(connection)
    if complete and actual != REACT_CONNECTIONS:
        missing = sorted(f"{target}.{handle}" for _, _, target, handle in REACT_CONNECTIONS - actual)
        raise ValueError(f"Connect {', '.join(missing)} before running.")
    return value


class LocalApp:
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.runs = RunHistory(self.path.parent / ".nreact" / "runs")

    def _load(self):
        before = revision(self.path)
        config = load_config(self.path, missing_ok=True)
        if before != revision(self.path):
            raise ConflictError("Configuration changed on disk. Reload and try again.")
        return config, before

    def snapshot(self) -> dict:
        with self.lock:
            config, version = self._load()
            public = parse_config(config.public_dict(), self.path, use_environment=False)
            return {"config": config.public_dict(), "path": str(self.path), "revision": version,
                    "toml": dumps_config(public), "graph_schema": GRAPH_SCHEMA,
                    "key_status": "saved" if config.model.api_key else "environment" if config.api_key() else "empty"}

    def save(self, payload: dict) -> dict:
        with self.lock:
            current, version = self._load()
            if payload.get("revision") != version:
                raise ConflictError("Configuration changed on disk. Reload before saving.")
            data = copy.deepcopy(payload.get("config"))
            if "toml" in payload:
                try:
                    raw = payload["toml"]
                    if not isinstance(raw, str) or len(raw.encode()) > 65_536:
                        raise ValueError()
                    data = tomllib.loads(raw)
                except (TypeError, ValueError, UnicodeError):
                    raise ValueError("Invalid TOML. Check the syntax and field types.") from None
                if "api_key" in data.get("model", {}):
                    raise ValueError("Edit API keys in Model properties; the TOML editor omits saved keys.")
                data.setdefault("model", {})
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
        task = payload.get("task", "")
        demo = payload.get("demo", False)
        stepping = payload.get("single_step", False)
        if type(demo) is not bool or type(stepping) is not bool:
            raise ValueError("Run options must be booleans.")
        if not demo and (not isinstance(task, str) or not task.strip() or len(task) > 16_000):
            raise ValueError("Enter a task of 1-16000 characters.")
        with self.lock:
            config, version = self._load()
            if not demo:
                if "connections" in payload:
                    validate_connections(payload["connections"], complete=True)
                if version == "missing" or payload.get("revision") != version:
                    raise ConflictError("Save or reload the configuration before running.")
                if not config.model.name.strip():
                    raise ValueError("Set a model name before running.")
            return self.runs.start(config, task, demo=demo, single_step=stepping)

    def export(self, payload: dict) -> dict:
        """Export to an exclusive local file, including in browsers without downloads."""
        kind = payload.get("kind")
        if kind == "config":
            content = self.snapshot()["toml"]
            stem, suffix = "nreact", "toml"
        elif kind == "run":
            record = self.runs.snapshot(payload.get("id"))
            content = json.dumps(record, ensure_ascii=False, indent=2)
            stem, suffix = f"run-{record['id'][:8]}", "json"
        elif kind == "graph":
            graph = payload.get("graph")
            if not isinstance(graph, dict) or set(graph) != {"version", "positions", "connections"} or graph["version"] != GRAPH_SCHEMA["version"]:
                raise ValueError("Unsupported graph document.")
            validate_connections(graph["connections"])
            if not isinstance(graph["positions"], dict):
                raise ValueError("Graph positions must be an object.")
            content = json.dumps(graph, ensure_ascii=False, indent=2)
            stem, suffix = "graph", "json"
        else:
            raise ValueError("Choose a configuration, graph or run to export.")
        directory = self.path.parent / ".nreact" / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}-{secrets.token_hex(6)}.{suffix}"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as writer:
            writer.write(content)
        return {"path": str(path), "content": content, "name": path.name}

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
                elif route == "/api/runs":
                    self._json(200, app.runs.list())
                elif route == "/api/run":
                    identity = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                    self._json(200, app.runs.snapshot(identity))
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
                    content_type = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                                    ".woff2": "font/woff2", ".woff": "font/woff"}.get(suffix)
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
                elif route == "/api/run/control":
                    self._json(200, app.runs.command(payload.get("id"), payload.get("action")))
                elif route == "/api/export":
                    self._json(200, app.export(payload))
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
    server.app = app
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
