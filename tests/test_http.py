import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from nreact import Agent, ChatModel, ModelError, ToolEnvironment


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = {"choices": [{"message": {"content": "Action: Finish[ok]"}}],
                      "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
        self.status = 200
        case = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                self.send_response(case.status)
                if case.status == 302:
                    self.send_header("Location", "/leak-target")
                self.end_headers()
                response = case.reply(body) if callable(case.reply) else case.reply
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_request_and_usage(self):
        result = ChatModel("test-model", base_url=self.url, api_key="test-only-key").generate("prompt", stop=["END"])
        self.assertEqual(result.text, "Action: Finish[ok]")
        self.assertEqual(result.usage["prompt_tokens"], 7)
        request = self.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["body"]["stop"], ["END"])
        self.assertEqual(request["authorization"], "Bearer test-only-key")

    def test_optional_stop_and_local_proxy_bypass(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            ChatModel("test", base_url=self.url, send_stop=False).generate("prompt", stop=["END"])
        self.assertNotIn("stop", self.requests[0]["body"])

    def test_http_errors_exclude_server_body(self):
        self.status, self.reply = 401, {"error": "test-only-key"}
        with self.assertRaises(ModelError) as raised:
            ChatModel("test", base_url=self.url).generate("prompt", stop=[])
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("test-only-key", str(raised.exception))
        result = Agent(ChatModel("test", base_url=self.url), ToolEnvironment([])).run("Task")
        self.assertEqual(result.status, "model_error")
        self.assertIn("401", result.error)
        self.assertNotIn("test-only-key", json.dumps(result.to_dict()))

    def test_redirect_is_not_followed(self):
        self.status = 302
        with self.assertRaises(ModelError):
            ChatModel("test", base_url=self.url, api_key="test-only-key").generate("prompt", stop=[])
        self.assertEqual(len(self.requests), 1)

    def test_invalid_response(self):
        for reply in [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}]:
            self.reply = reply
            with self.assertRaises(ModelError):
                ChatModel("test", base_url=self.url).generate("prompt", stop=[])

    def test_invalid_configuration(self):
        for url in ["http://example.org/v1", "https://user:secret@example.org", "https://example.org?key=secret", "file:///tmp/x"]:
            with self.assertRaises(ValueError):
                ChatModel("test", base_url=url)

    def test_cli_http_tool_feedback_and_finish(self):
        def response(body):
            prompt = body["messages"][-1]["content"]
            text = ("Thought 2: The note says blue.\nAction 2: Finish[blue]"
                    if "Observation 1: blue" in prompt else "Thought 1: Read the note.\nAction 1: Read[note.txt]")
            return {"choices": [{"message": {"content": text}}]}
        self.reply = response
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "note.txt").write_text("blue", encoding="utf-8")
            env = {**os.environ, "NREACT_BASE_URL": self.url, "NREACT_MODEL": "test-fixture",
                   "NREACT_API_KEY": "test-only-key", "NREACT_AUTH": "api_key"}
            trace = Path(directory) / "trace.jsonl"
            result = subprocess.run([sys.executable, "-m", "nreact", "run", "Read note.txt", "--workspace", directory,
                                     "--trace", str(trace), "--json"], env=env, cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["answer"], "blue")
            self.assertNotIn("test-only-key", trace.read_text(encoding="utf-8"))
            self.assertEqual(len(self.requests), 2)
