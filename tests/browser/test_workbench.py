"""Real Chromium tests against the packaged Vue app and local Python server.

Install development tools with ``pip install playwright==1.57.0`` and
``python -m playwright install chromium``, then run
``python -m unittest discover -s tests/browser -v``.
Only scripted demo models are used; no external model requests are made.
"""

import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from nreact import Agent, ScriptedModel, ToolEnvironment
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
        self.page.set_default_timeout(10000)
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
        expect(self.page.get_by_role("textbox", name="TOML configuration")).to_have_value(re.compile('name = "browser-saved"'))
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
        exported = json.loads(self.page.get_by_role("textbox", name="Export content").input_value())
        self.assertEqual(exported["version"], 2)
        self.assertEqual(len(exported["connections"]), 4)
        self.page.get_by_role("button", name="Close export", exact=True).click()
        self.demo()
        self.page.get_by_role("button", name="Export run", exact=True).click()
        exported = json.loads(self.page.get_by_role("textbox", name="Export content").input_value())
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

    def save_workflow(self, name="Browser workflow"):
        self.menu("File", "Save workflow…")
        self.page.get_by_role("textbox", name="Workflow name", exact=True).fill(name)
        self.page.get_by_role("button", name="Save workflow", exact=True).click()
        expect(self.page.get_by_role("dialog", name="Workflows")).to_have_count(0)

    def graph_record(self):
        return self.page.evaluate("key => JSON.parse(localStorage.getItem(key))", f"nreact-graph:{self.config_path}")

    def workflow_fixture(self):
        return {"format": "nreact.workflow", "version": 1, "name": "Imported workflow", "task": "An imported task",
                "config": self.server.app.snapshot()["config"],
                "graph": {"version": 2, "positions": {"model": {"x": 70, "y": 30}},
                          "connections": self.server.app.snapshot()["graph_schema"]["connections"]}}

    def import_document(self, document):
        self.page.locator('input[type="file"]').set_input_files({
            "name": "workflow.json", "mimeType": "application/json",
            "buffer": json.dumps(document).encode(),
        })

    def test_saved_workflow_restores_task_config_and_graph(self):
        self.page.get_by_role("textbox", name="task", exact=True).fill("Saved task")
        node = self.page.locator('.vue-flow__node[data-id="model"]')
        node.focus()
        node.press("ArrowRight")
        self.save_workflow()
        self.page.get_by_role("textbox", name="task", exact=True).fill("Unsaved replacement")
        self.menu("File", "Open workflow…")
        self.page.get_by_role("button", name="Open selected workflow", exact=True).click()
        expect(self.page.get_by_role("textbox", name="Workflow task preview", exact=True)).to_have_value("Saved task")
        self.page.get_by_role("button", name="Replace working copy", exact=True).click()
        expect(self.page.get_by_role("textbox", name="task", exact=True)).to_have_value("Saved task")
        self.assertEqual(self.graph_record()["positions"]["model"]["x"], 10)
        expect(self.page.locator(".statusbar strong")).to_have_text("Unsaved changes")
        expect(self.page.locator(".top-actions").get_by_role("button", name="Run", exact=True)).to_be_disabled()
        self.page.locator(".top-actions").get_by_role("button", name="Save configuration", exact=True).click()
        expect(self.page.locator(".statusbar strong")).to_have_text("Ready")
        self.assertEqual(self.server.app.runs.list()["runs"], [])

    def test_import_preview_clears_credentials_and_leaves_disk_unchanged_until_save(self):
        snapshot = self.server.app.snapshot()
        snapshot["config"]["model"]["api_key"] = "test-only-secret"
        self.server.app.save({"config": snapshot["config"], "revision": snapshot["revision"], "api_key_action": "replace"})
        self.page.reload()
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)
        before = self.config_path.read_bytes()
        document = self.workflow_fixture()
        document["config"]["model"].update(api_key_env="PRIVATE_TOKEN", auth="chatgpt", auth_file="private.json")
        self.import_document(document)
        expect(self.page.get_by_role("textbox", name="Workflow settings preview")).to_have_value(re.compile('api_key_env = ""'))
        self.assertEqual(self.config_path.read_bytes(), before)
        self.page.get_by_role("button", name="Replace working copy", exact=True).click()
        expect(self.page.get_by_role("textbox", name="task", exact=True)).to_have_value("An imported task")
        self.assertEqual(self.config_path.read_bytes(), before)
        expect(self.page.get_by_role("textbox", name="model.api_key_env", exact=True)).to_have_value("")
        self.page.locator(".top-actions").get_by_role("button", name="Save configuration", exact=True).click()
        expect(self.page.locator(".statusbar strong")).to_have_text("Ready")
        self.assertNotIn("test-only-secret", self.config_path.read_text())
        self.assertEqual(self.server.app.runs.list()["runs"], [])

    def test_cancel_import_preserves_drafts(self):
        self.page.get_by_role("textbox", name="task", exact=True).fill("Keep my task")
        self.import_document(self.workflow_fixture())
        self.page.get_by_role("button", name="Keep editing", exact=True).click()
        expect(self.page.get_by_role("textbox", name="task", exact=True)).to_have_value("Keep my task")
        self.assertEqual(self.server.app.workflows.list()["total"], 0)

    def test_invalid_import_keeps_existing_graph(self):
        self.page.get_by_role("textbox", name="task", exact=True).fill("Original")
        value = self.workflow_fixture()
        value["graph"]["positions"]["model"]["x"] = "broken"
        self.import_document(value)
        expect(self.page.get_by_role("alert")).to_contain_text("coordinates")
        expect(self.page.get_by_role("textbox", name="task", exact=True)).to_have_value("Original")
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)

    def test_workflow_export_can_be_reimported(self):
        self.page.get_by_role("textbox", name="task", exact=True).fill("Portable task")
        self.menu("File", "Export workflow")
        data = json.loads(self.page.get_by_role("textbox", name="Export content").input_value())
        self.assertEqual(data["task"], "Portable task")
        self.assertNotIn("api_key", data["config"]["model"])
        self.page.get_by_role("button", name="Close export", exact=True).click()
        self.import_document(data)
        expect(self.page.get_by_role("textbox", name="Workflow task preview")).to_have_value("Portable task")

    def test_workflow_delete_requires_confirmation(self):
        self.save_workflow()
        self.menu("File", "Open workflow…")
        self.page.get_by_role("button", name="Delete workflow", exact=True).click()
        self.page.get_by_role("button", name="Keep workflow", exact=True).click()
        self.assertEqual(self.server.app.workflows.list()["total"], 1)
        self.menu("File", "Open workflow…")
        self.page.get_by_role("button", name="Delete workflow", exact=True).click()
        self.page.get_by_role("button", name="Confirm deletion", exact=True).click()
        expect(self.page.get_by_role("dialog", name="Workflows")).to_have_count(0)
        self.assertEqual(self.server.app.workflows.list()["total"], 0)
        self.assertTrue(self.config_path.exists())

    def test_stale_workflow_save_preserves_newer_version_and_local_draft(self):
        self.save_workflow()
        item = self.server.app.workflows.list()["workflows"][0]
        newer = self.server.app.workflows.get(item["id"])
        newer["workflow"]["task"] = "Changed in another tab"
        self.server.app.workflows.save(newer)
        self.page.get_by_role("textbox", name="task", exact=True).fill("My unsaved draft")
        self.menu("File", "Save workflow…")
        self.page.get_by_role("button", name="Save workflow", exact=True).click()
        expect(self.page.get_by_role("dialog", name="Workflows").get_by_role("alert")).to_contain_text("changed on disk")
        self.page.get_by_role("button", name="Close workflows", exact=True).click()
        expect(self.page.get_by_role("textbox", name="task", exact=True)).to_have_value("My unsaved draft")
        self.assertEqual(self.server.app.workflows.get(item["id"])["workflow"]["task"], "Changed in another tab")

    def test_movement_reset_undo_and_redo(self):
        node = self.page.locator('.vue-flow__node[data-id="model"]')
        node.focus()
        node.press("ArrowRight")
        node.press("ArrowDown")
        self.page.get_by_role("button", name="Undo graph change", exact=True).click()
        self.assertEqual(self.graph_record()["positions"]["model"], {"x": 10, "y": 0})
        self.page.get_by_role("button", name="Redo graph change", exact=True).click()
        self.assertEqual(self.graph_record()["positions"]["model"], {"x": 10, "y": 10})
        self.menu("View", "Reset node positions")
        self.assertEqual(self.graph_record()["positions"], {})
        self.page.get_by_role("button", name="Undo graph change", exact=True).click()
        self.assertEqual(self.graph_record()["positions"]["model"], {"x": 10, "y": 10})

    def test_readonly_snapshot_movement_keeps_working_layout(self):
        node = self.page.locator('.vue-flow__node[data-id="model"]')
        node.focus()
        node.press("ArrowRight")
        before = self.graph_record()
        self.demo()
        node.focus()
        node.press("ArrowDown")
        self.assertEqual(self.graph_record(), before)
        self.page.locator(".workspace-tabs").get_by_role("button", name="Working copy", exact=True).click()
        self.assertEqual(self.graph_record(), before)
        self.menu("File", "Export working graph")
        data = json.loads(self.page.get_by_role("textbox", name="Export content").input_value())
        self.assertEqual(data["positions"]["model"], {"x": 10, "y": 0})

    def test_corrupt_saved_graph_recovers_to_usable_canvas(self):
        value = {"version": 2, "positions": {"model": {"x": "bad", "y": 10}}, "connections": []}
        self.page.evaluate("([key, value]) => localStorage.setItem(key, JSON.stringify(value))", [f"nreact-graph:{self.config_path}", value])
        self.page.reload()
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)
        expect(self.page.locator(".vue-flow__edge")).to_have_count(4)
        expect(self.page.get_by_text("Saved diagram could not be read. Using the initial layout.", exact=True)).to_be_visible()

    def test_viewport_zoom_survives_reload(self):
        self.page.get_by_role("button", name="Zoom in", exact=True).click()
        self.page.wait_for_function("key => JSON.parse(localStorage.getItem(key) || 'null')?.viewport?.zoom > 0", arg=f"nreact-graph:{self.config_path}")
        before = self.graph_record()["viewport"]
        self.page.reload()
        expect(self.page.locator(".vue-flow__node")).to_have_count(5)
        self.page.wait_for_function("([key, zoom]) => Math.abs(JSON.parse(localStorage.getItem(key) || 'null')?.viewport?.zoom - zoom) < .00001", arg=[f"nreact-graph:{self.config_path}", before["zoom"]])
        self.assertAlmostEqual(self.graph_record()["viewport"]["zoom"], before["zoom"], places=5)

    def test_queue_cancel_and_resume_execute_each_remaining_task_once(self):
        built = []
        def build(config):
            built.append(config.model.name)
            return Agent(ScriptedModel(["Thought: Done\nAction: Finish[queue answer]"]), ToolEnvironment([]))
        with patch('nreact.runs.build_agent', side_effect=build):
            self.menu('Run', 'Pause queue')
            self.page.get_by_role('textbox', name='task', exact=True).fill('First queued task')
            self.menu('Run', 'Queue task')
            expect(self.page.locator('.statusbar strong')).to_have_text('queued')
            expect(self.page.get_by_label('Queue status')).to_contain_text('Queue: 1')
            self.page.locator('.workspace-tabs').get_by_role('button', name='Working copy', exact=True).click()
            self.page.get_by_role('textbox', name='task', exact=True).fill('Removed task')
            self.menu('Run', 'Queue task')
            expect(self.page.get_by_label('Queue status')).to_contain_text('Queue: 2')
            self.page.get_by_role('button', name='Stop', exact=True).click()
            expect(self.page.locator('.statusbar strong')).to_have_text('cancelled')
            expect(self.page.get_by_label('Queue status')).to_contain_text('Queue: 1')
            self.assertEqual(built, [])
            self.menu('Run', 'Resume queue')
            self.page.get_by_role('button', name='Run history', exact=True).click()
            self.page.locator('.run-list button').filter(has_text='First queued task').click()
            expect(self.page.locator('.statusbar strong')).to_have_text('finished')
            self.page.locator('.trace-tabs').get_by_role('button', name='Result', exact=True).click()
            expect(self.page.locator('.result-output pre')).to_have_text('queue answer')
            self.assertEqual(built, ['fixture-model'])

    def test_queue_failure_pauses_pending_work_until_explicit_resume(self):
        built = []
        class FailingModel:
            def generate(self, prompt, *, stop):
                raise RuntimeError('fixture-secret')
        def build(config):
            built.append(config.model.name)
            model = FailingModel() if len(built) == 1 else ScriptedModel(['Thought: Done\nAction: Finish[recovered]'])
            return Agent(model, ToolEnvironment([]))
        with patch('nreact.runs.build_agent', side_effect=build):
            self.page.get_by_role('textbox', name='task', exact=True).fill('Fail first')
            self.menu('Run', 'Queue task')
            expect(self.page.locator('.statusbar strong')).to_have_text('model_error')
            expect(self.page.get_by_label('Queue status')).to_contain_text('paused')
            self.page.locator('.workspace-tabs').get_by_role('button', name='Working copy', exact=True).click()
            self.page.get_by_role('textbox', name='task', exact=True).fill('Continue after repair')
            self.menu('Run', 'Queue task')
            expect(self.page.locator('.statusbar strong')).to_have_text('queued')
            self.assertEqual(len(built), 1)
            self.menu('Run', 'Resume queue')
            expect(self.page.locator('.statusbar strong')).to_have_text('finished')
            self.assertEqual(len(built), 2)

    def test_other_browser_run_is_discovered_without_replacing_working_draft(self):
        self.page.get_by_role('textbox', name='task', exact=True).fill('Keep my local task')
        other_context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        other = other_context.new_page()
        try:
            other.goto(self.page.url)
            expect(other.locator('.vue-flow__node')).to_have_count(5)
            other.locator('.menus').get_by_role('button', name='Run', exact=True).click()
            other.locator('.dropdown').get_by_role('button', name='Step through offline demo', exact=True).click()
            expect(other.locator('.statusbar strong')).to_have_text('paused')
            self.page.bring_to_front()
            expect(self.page.get_by_role('button', name='Return to active run', exact=True)).to_be_visible(timeout=10000)
            expect(self.page.get_by_role('textbox', name='task', exact=True)).to_have_value('Keep my local task')
            self.page.get_by_role('button', name='Return to active run', exact=True).click()
            expect(self.page.locator('.statusbar strong')).to_have_text('paused')
        finally:
            other_context.close()

    def test_read_connection_recovers_and_keeps_the_task_draft(self):
        self.page.get_by_role('textbox', name='task', exact=True).fill('Preserve this draft')
        self.page.route('**/api/runs/state', lambda route: route.abort('failed'))
        expect(self.page.get_by_role('alert')).to_contain_text('could not be reached', timeout=10000)
        self.page.unroute('**/api/runs/state')
        expect(self.page.get_by_role('alert')).to_have_count(0, timeout=10000)
        expect(self.page.get_by_role('textbox', name='task', exact=True)).to_have_value('Preserve this draft')

    def test_lost_post_response_is_never_automatically_resubmitted(self):
        calls = []
        def lose_response(route):
            calls.append(route.request.post_data_json)
            route.fetch()
            route.abort('failed')
        self.page.route('**/api/run', lose_response)
        self.menu('Run', 'Step through offline demo')
        expect(self.page.get_by_role('alert')).to_contain_text('may have been applied')
        expect(self.page.get_by_role('button', name='Return to active run', exact=True)).to_be_visible(timeout=10000)
        self.page.get_by_role('button', name='Return to active run', exact=True).click()
        expect(self.page.locator('.statusbar strong')).to_have_text('paused')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.server.app.runs.list()['runs']), 1)
        self.page.unroute('**/api/run')

    def test_active_polling_uses_event_deltas_and_does_not_repeat_history_scan(self):
        self.demo(step=True)
        seen = []
        self.page.on('request', lambda request: seen.append(request.url))
        self.page.wait_for_function("() => document.querySelector('.statusbar strong').textContent === 'paused'")
        with self.page.expect_response(lambda response: '/api/run/updates?' in response.url) as received:
            pass
        body = received.value.json()
        self.assertEqual(body['events'], [])
        self.assertEqual(body['next_offset'], 3)
        self.assertNotIn('config', body)
        self.assertFalse(any(url.endswith('/api/runs') for url in seen))

    def test_state_polling_keeps_working_copy_during_execution(self):
        self.demo(step=True)
        self.page.locator('.workspace-tabs').get_by_role('button', name='Working copy', exact=True).click()
        self.page.get_by_role('textbox', name='task', exact=True).fill('Working during execution')
        with self.page.expect_response(lambda response: '/api/runs/state' in response.url):
            pass
        expect(self.page.locator('.snapshot-label')).to_have_count(0)
        expect(self.page.get_by_role('textbox', name='task', exact=True)).to_have_value('Working during execution')


if __name__ == "__main__":
    unittest.main()
