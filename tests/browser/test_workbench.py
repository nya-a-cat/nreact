"""Real Chromium tests against the packaged Vue app and local Python server.

Install development tools with ``pip install playwright==1.57.0`` and
``python -m playwright install chromium``, then run
``python -m unittest discover -s tests/browser -v``.
Only scripted demo models are used; no external model requests are made.
"""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from nreact.config import parse_config, save_config
from nreact.web import make_server


class WorkbenchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "nreact.toml"
        save_config(parse_config({"model": {"name": "fixture-model"}}, self.config_path, use_environment=False))
        self.server = make_server(self.config_path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.page_errors, self.console, self.responses = [], [], []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("console", lambda message: self.console.append({"type": message.type, "text": message.text}))
        self.page.on("response", lambda response: self.responses.append({"url": response.url, "status": response.status}))
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}")
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)
        expect(self.page.locator(".loading")).to_have_count(0)

    def tearDown(self):
        output = Path(os.environ.get("NREACT_BROWSER_ARTIFACTS", "browser-artifacts")) / self._testMethodName
        output.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(output / "page.png"), full_page=True)
            (output / "console.json").write_text(json.dumps(self.console, indent=2), encoding="utf-8")
            (output / "page-errors.json").write_text(json.dumps(self.page_errors, indent=2), encoding="utf-8")
            (output / "responses.json").write_text(json.dumps(self.responses, indent=2), encoding="utf-8")
            self.context.tracing.stop(path=str(output / "trace.zip"))
        finally:
            self.context.close()
            with self.server.app.runs.lock:
                identity = self.server.app.runs.active
                if identity:
                    self.server.app.runs.command(identity, "cancel")
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)
            self.temporary.cleanup()
        self.assertEqual(self.page_errors, [], "Browser JavaScript errors were recorded.")

    def menu(self, name, command):
        self.page.locator(".menus").get_by_role("button", name=name, exact=True).click()
        self.page.locator(".dropdown").get_by_role("button", name=command, exact=True).click()

    def demo(self, *, step=False):
        self.menu("Run", "Step through offline demo" if step else "Run offline demo")
        expect(self.page.locator(".statusbar strong")).to_have_text("paused" if step else "finished")

    def test_workspace_and_bundled_assets(self):
        expect(self.page.get_by_role("navigation", name="Application menu")).to_be_visible()
        expect(self.page.get_by_role("button", name="Settings", exact=True)).to_be_visible()
        expect(self.page.locator(".statusbar strong")).to_have_text("Ready")
        expect(self.page.locator("vite-error-overlay")).to_have_count(0)
        self.assertTrue(all(response["status"] < 400 for response in self.responses))

    def test_demo_step_resume_and_replay(self):
        self.demo(step=True)
        expect(self.page.locator(".event-list button")).to_have_count(3)
        self.page.locator(".top-actions").get_by_role("button", name="Step", exact=True).click()
        expect(self.page.locator(".event-list button")).to_have_count(6)
        expect(self.page.locator(".statusbar strong")).to_have_text("paused")
        self.page.locator(".top-actions").get_by_role("button", name="Resume", exact=True).click()
        expect(self.page.locator(".statusbar strong")).to_have_text("finished")
        expect(self.page.locator(".event-list button")).to_have_count(8)
        self.page.get_by_role("button", name="Previous event", exact=True).click()
        expect(self.page.locator(".trace-nav")).to_contain_text("7 / 8")
        self.page.locator(".trace-tabs").get_by_role("button", name="Result", exact=True).click()
        expect(self.page.locator(".result-output pre")).to_have_text("Harbor City")
        expect(self.page.locator(".result-stats")).to_contain_text("3 model calls")

    def test_stop_paused_run(self):
        self.demo(step=True)
        self.page.get_by_role("button", name="Stop", exact=True).click()
        expect(self.page.locator(".statusbar strong")).to_have_text("cancelled")
        expect(self.page.get_by_role("button", name="Stop", exact=True)).to_be_disabled()
        expect(self.page.locator(".event-list button")).to_have_count(3)

    def test_toml_save_and_reload(self):
        self.page.get_by_role("button", name="TOML source", exact=True).click()
        self.page.get_by_role("textbox", name="TOML configuration").fill('[model]\nname = "browser-saved"\n\n[agent]\nmax_steps = 9\n')
        self.page.get_by_role("button", name="Save TOML", exact=True).click()
        expect(self.page.locator(".statusbar strong")).to_have_text("Ready")
        self.page.reload()
        expect(self.page.locator('.vue-flow__node[data-id="model"]')).to_contain_text("ChatModel")
        self.page.get_by_role("button", name="TOML source", exact=True).click()
        expect(self.page.get_by_role("textbox", name="TOML configuration")).to_contain_text('name = "browser-saved"')
        self.assertIn('name = "browser-saved"', self.config_path.read_text(encoding="utf-8"))

    def test_invalid_toml_keeps_previous_file(self):
        before = self.config_path.read_bytes()
        self.page.get_by_role("button", name="TOML source", exact=True).click()
        self.page.get_by_role("textbox", name="TOML configuration").fill("[model\nname = invalid")
        self.page.get_by_role("button", name="Save TOML", exact=True).click()
        expect(self.page.get_by_role("alert")).to_contain_text("Invalid TOML")
        self.assertEqual(self.config_path.read_bytes(), before)
        self.menu("File", "Reload from disk")
        self.page.get_by_role("button", name="Keep editing", exact=True).click()
        expect(self.page.get_by_role("textbox", name="TOML configuration")).to_have_value("[model\nname = invalid")

    def test_exports_contain_real_graph_and_result(self):
        self.menu("File", "Export working graph")
        exported = json.loads(self.page.get_by_role("textbox", name="Export contents").input_value())
        self.assertEqual(exported["version"], 2)
        self.assertEqual(len(exported["connections"]), 4)
        self.page.get_by_role("button", name="Close export", exact=True).click()
        self.demo()
        self.page.get_by_role("button", name="Export run", exact=True).click()
        exported = json.loads(self.page.get_by_role("textbox", name="Export contents").input_value())
        self.assertEqual(exported["result"]["answer"], "Harbor City")
        self.assertEqual(exported["status"], "finished")
        path = self.page.get_by_role("textbox", name="Export path").input_value()
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["id"], exported["id"])

    def test_keyboard_node_movement_persists(self):
        node = self.page.locator('.vue-flow__node[data-id="model"]')
        node.focus()
        node.press("ArrowRight")
        node.press("ArrowDown")
        key = f"nreact-graph:{self.config_path}"
        before = self.page.evaluate("key => JSON.parse(localStorage.getItem(key))", key)
        self.assertEqual(before["positions"]["model"], {"x": 10, "y": 10})
        self.page.reload()
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)
        after = self.page.evaluate("key => JSON.parse(localStorage.getItem(key))", key)
        self.assertEqual(before, after)

    def test_narrow_window_and_dialog_focus(self):
        self.page.set_viewport_size({"width": 800, "height": 700})
        self.page.get_by_role("button", name="Components", exact=True).click()
        expect(self.page.locator(".library")).to_be_visible()
        self.page.get_by_role("button", name="Hide side panel", exact=True).click()
        expect(self.page.locator(".library")).to_have_count(0)
        settings = self.page.get_by_role("button", name="Settings", exact=True)
        settings.click()
        expect(self.page.get_by_role("dialog")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        expect(settings).to_be_focused()


if __name__ == "__main__":
    unittest.main()
