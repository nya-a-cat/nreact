import json
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from nreact import ScriptedModel
from nreact.config import load_config
from nreact.web import make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "nreact.toml"
        self.server = make_server(self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.opener.open(self.url) as response:
            self.html = response.read().decode()
        self.token = re.search(r'name="nreact-token" content="([^"]+)"', self.html)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, route, body=None, headers=None):
        request = urllib.request.Request(self.url + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Nreact-Token": self.token, "Content-Type": "application/json", "Origin": self.url, **(headers or {})})
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_packaged_vue_assets_and_response_headers(self):
        self.assertIn('id="app"', self.html)
        self.assertNotIn("__NREACT_TOKEN__", self.html)
        asset = re.search(r'src="(/assets/[^"]+\.js)"', self.html)[1]
        with self.opener.open(self.url + asset) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_key_write_redaction_preservation_and_removal(self):
        status, snapshot = self.request("/api/config")
        self.assertEqual(status, 200)
        config = snapshot["config"]
        config["model"]["name"] = "fixture"
        config["model"]["api_key"] = "fake-key-for-tests"
        status, saved = self.request("/api/config", {"config": config, "revision": snapshot["revision"], "api_key_action": "replace"})
        self.assertEqual(status, 200)
        self.assertEqual(load_config(self.path).model.api_key, "fake-key-for-tests")
        self.assertEqual(saved["key_status"], "saved")
        self.assertNotIn("fake-key-for-tests", json.dumps(saved))
        saved["config"]["model"]["name"] = "changed"
        status, preserved = self.request("/api/config", {"config": saved["config"], "revision": saved["revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(load_config(self.path).model.api_key, "fake-key-for-tests")
        status, cleared = self.request("/api/config", {"config": preserved["config"], "revision": preserved["revision"], "api_key_action": "clear"})
        self.assertEqual(status, 200)
        self.assertEqual(load_config(self.path).model.api_key, "")

    def test_cross_origin_host_and_csrf_rejected(self):
        for headers in [{"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"X-Nreact-Token": "wrong"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/config", {}, headers)[0], 403)
        self.assertFalse(self.path.exists())

    def test_disk_conflict_keeps_external_edit(self):
        _, snapshot = self.request("/api/config")
        self.path.write_text('[model]\nname = "external"\n', encoding="utf-8")
        status, _ = self.request("/api/config", {"config": snapshot["config"], "revision": snapshot["revision"]})
        self.assertEqual(status, 409)
        self.assertEqual(load_config(self.path).model.name, "external")

    def test_invalid_configuration_never_writes(self):
        _, snapshot = self.request("/api/config")
        snapshot["config"]["model"]["timeout"] = -1
        status, _ = self.request("/api/config", {"config": snapshot["config"], "revision": snapshot["revision"]})
        self.assertEqual(status, 400)
        self.assertFalse(self.path.exists())

    def test_no_arbitrary_static_file_access(self):
        for route in ["/nreact.toml", "/assets/../nreact.toml", "/assets/..\\nreact.toml", "/api/unknown"]:
            self.assertEqual(self.request(route)[0], 404)

    def test_saved_configuration_drives_agent_run(self):
        _, snapshot = self.request("/api/config")
        snapshot["config"]["model"]["name"] = "fixture"
        snapshot["config"]["tools"]["wikipedia"] = False
        snapshot["config"]["tools"]["workspace"] = "."
        (self.path.parent / "note.txt").write_text("blue", encoding="utf-8")
        _, saved = self.request("/api/config", {"config": snapshot["config"], "revision": snapshot["revision"]})
        outputs = ["Thought: Read\nAction: Read[note.txt]", "Thought: Blue\nAction: Finish[blue]"]
        with patch("nreact.config.ChatModel", return_value=ScriptedModel(outputs)):
            status, _ = self.request("/api/run", {"task": "Read note.txt", "revision": saved["revision"]})
            self.assertEqual(status, 202)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                _, state = self.request("/api/run")
                if state["status"] != "running":
                    break
                time.sleep(.01)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["result"]["answer"], "blue")
        self.assertTrue(any(event["kind"] == "observation" and event["text"] == "blue" for event in state["events"]))

    def test_missing_config_cannot_run(self):
        self.assertEqual(self.request("/api/run", {"task": "Test", "revision": "missing"})[0], 409)
