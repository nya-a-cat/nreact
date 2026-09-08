import http.client
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from nreact.web import make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "nreact.toml"
        self.server = make_server(self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.server.app.token

    def tearDown(self):
        if self.server.app.runs.active:
            self.server.app.runs.command(self.server.app.runs.active, "cancel")
            self.eventually(lambda: self.server.app.runs.active is None)
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.temporary.cleanup()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = {"X-Nreact-Token": self.token}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        try:
            connection.request("GET" if data is None else "POST", path,
                               body=None if data is None else json.dumps(data), headers=request_headers)
            response = connection.getresponse()
            body = response.read().decode()
            return response.status, body, dict(response.getheaders())
        finally:
            connection.close()

    def data(self, path, payload=None):
        status, body, _ = self.request(path, payload)
        self.assertLess(status, 300, body)
        return json.loads(body)

    def eventually(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.005)
        self.fail("Expected run state was not reached.")

    def test_assets_and_request_guards(self):
        status, html, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(self.token, html)
        self.assertNotIn("__NREACT_TOKEN__", html)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html):
            self.assertEqual(self.request(asset)[0], 200)
        self.assertEqual(self.request('/api/config', headers={"X-Nreact-Token": ""})[0], 403)
        self.assertEqual(self.request('/api/config', headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request('/api/config', headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request('/assets/../config.py')[0], 404)

    def test_property_and_toml_save_preserve_keys_and_reject_stale_writes(self):
        snapshot = self.data('/api/config')
        self.assertEqual(snapshot['config']['ui']['theme'], 'classic')
        snapshot['config']['model'].update(name='fixture', api_key='fixture-secret')
        saved = self.data('/api/config', {"config": snapshot['config'], "revision": snapshot['revision'], "api_key_action": "replace"})
        self.assertNotIn('fixture-secret', json.dumps(saved))
        self.assertEqual(saved['key_status'], 'saved')
        self.assertEqual(self.request('/api/config', {"config": snapshot['config'], "revision": snapshot['revision']})[0], 409)
        changed_toml = saved['toml'].replace('max_steps = 20', 'max_steps = 7').replace('theme = "classic"', 'theme = "graphite"')
        changed = self.data('/api/config', {"toml": changed_toml, "revision": saved['revision']})
        self.assertEqual(changed['config']['agent']['max_steps'], 7)
        self.assertEqual(changed['config']['ui']['theme'], 'graphite')
        self.assertEqual(self.data('/api/config')['config']['ui']['theme'], 'graphite')
        self.assertIn('fixture-secret', self.path.read_text())
        invalid_theme = changed['toml'].replace('theme = "graphite"', 'theme = "missing"')
        self.assertEqual(self.request('/api/config', {"toml": invalid_theme, "revision": changed['revision']})[0], 400)
        self.assertEqual(self.data('/api/config')['revision'], changed['revision'])
        self.assertEqual(self.request('/api/config', {"toml": '[model\nsecret', "revision": changed['revision']})[0], 400)
        self.assertEqual(self.request('/api/config', {"toml": '[model]\napi_key = "hidden"', "revision": changed['revision']})[0], 400)
        cleared = self.data('/api/config', {"config": changed['config'], "revision": changed['revision'], "api_key_action": "clear"})
        self.assertNotIn('fixture-secret', self.path.read_text())
        self.assertNotIn('api_key', cleared['config']['model'])
        self.assertEqual(cleared['config']['ui']['theme'], 'graphite')

    def test_demo_step_control_and_persistent_history(self):
        self.assertEqual(self.request('/api/run', {"task": 'task', "revision": 'missing'})[0], 409)
        identity = self.data('/api/run', {"demo": True, "single_step": True})['id']
        self.eventually(lambda: self.data(f'/api/run?id={identity}')['status'] == 'paused')
        first = self.data(f'/api/run?id={identity}')
        self.assertEqual(first['steps'], 1)
        self.assertEqual(len(first['events']), 3)
        self.data('/api/run/control', {"id": identity, "action": "step"})
        self.eventually(lambda: self.data(f'/api/run?id={identity}')['steps'] == 2)
        self.data('/api/run/control', {"id": identity, "action": "resume"})
        self.eventually(lambda: self.server.app.runs.active is None)
        final = self.data(f'/api/run?id={identity}')
        self.assertEqual(final['result']['answer'], 'Harbor City')
        self.assertEqual(final['result']['model_calls'], 3)
        self.assertEqual(self.data('/api/runs')['runs'][0]['id'], identity)
        self.assertEqual(self.request('/api/run/control', {"id": identity, "action": "step"})[0], 400)
        self.assertEqual(self.request('/api/run?id=../config')[0], 400)

    def test_stop_while_paused(self):
        identity = self.data('/api/run', {"demo": True, "single_step": True})['id']
        self.eventually(lambda: self.data(f'/api/run?id={identity}')['status'] == 'paused')
        self.data('/api/run/control', {"id": identity, "action": "cancel"})
        self.eventually(lambda: self.server.app.runs.active is None)
        result = self.data(f'/api/run?id={identity}')['result']
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['model_calls'], 1)
