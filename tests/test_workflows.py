import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact.config import parse_config
from nreact.graph import GRAPH_SCHEMA
from nreact.web import make_server
from nreact.workflows import WorkflowConflict, WorkflowStore, validate_connections, validate_graph, validate_workflow


def document():
    return {"format": "nreact.workflow", "version": 1, "name": "Read project", "task": "Read README.md",
            "config": parse_config({"model": {"name": "fixture"}}, use_environment=False).public_dict(),
            "graph": {"version": 2, "positions": {"model": {"x": 10, "y": 20}},
                      "connections": copy.deepcopy(GRAPH_SCHEMA["connections"])}}


class WorkflowValidationTests(unittest.TestCase):
    def test_roundtrip_is_isolated_and_unicode_safe(self):
        value = document()
        value["name"] = " 猫猫工作流 "
        result = validate_workflow(value)
        self.assertEqual(result["name"], "猫猫工作流")
        result["graph"]["positions"]["model"]["x"] = 50
        self.assertEqual(value["graph"]["positions"]["model"]["x"], 10)

    def test_bad_format_version_name_task_and_unknown_fields(self):
        bad = [None, [], {}, {**document(), "format": "comfy"}, {**document(), "version": True},
               {**document(), "version": 2}, {**document(), "name": "  "}, {**document(), "name": "a\nb"},
               {**document(), "name": "a" * 121}, {**document(), "task": []},
               {**document(), "task": "a" * 16001}, {**document(), "extra": {}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_workflow(value)

    def test_external_import_clears_all_local_credential_sources(self):
        value = document()
        value["config"]["model"].update(api_key_env="PRIVATE_KEY", auth="chatgpt", auth_file="/private/token.json")
        loaded = validate_workflow(value, imported=True)["config"]["model"]
        self.assertEqual((loaded["api_key_env"], loaded["auth"], loaded["auth_file"]), ("", "api_key", ""))
        self.assertEqual(validate_workflow(value)["config"]["model"]["auth"], "chatgpt")
        self.assertEqual(value["config"]["model"]["api_key_env"], "PRIVATE_KEY")

    def test_api_key_literals_are_rejected_even_when_empty(self):
        for key in ("fixture-secret", ""):
            value = document()
            value["config"]["model"]["api_key"] = key
            with self.assertRaisesRegex(ValueError, "model.api_key"):
                validate_workflow(value)

    def test_validation_does_not_import_or_execute_custom_tools(self):
        value = document()
        value["config"]["tools"]["custom"] = [{"name": "project_lookup", "description": "Look up", "callable": "nonexistent.module:lookup"}]
        with patch("nreact.config.importlib.import_module") as imported:
            self.assertEqual(validate_workflow(value)["config"]["tools"]["custom"][0]["name"], "project_lookup")
        imported.assert_not_called()

    def test_byte_limit_and_invalid_unicode(self):
        value = document()
        value["task"] = "猫" * 16000
        value["config"]["tools"]["custom"] = [{"name": f"tool{i}", "description": "x" * 2000, "callable": "tools:lookup"} for i in range(10)]
        with self.assertRaises(ValueError):
            validate_workflow(value)
        value = document()
        value["task"] = "\ud800"
        with self.assertRaises(ValueError):
            validate_workflow(value)

    def test_invalid_graph_positions_and_nonfinite_numbers(self):
        for positions in ([], {"unknown": {"x": 1, "y": 2}}, {"__proto__": {"x": 0, "y": 0}},
                          *({"model": {"x": x, "y": 0}} for x in (True, "1", None, float("nan"), float("inf"), 1000001))):
            with self.subTest(positions=positions), self.assertRaises(ValueError):
                validate_graph({**document()["graph"], "positions": positions})

    def test_connections_are_validated_even_in_incomplete_graph(self):
        edge = GRAPH_SCHEMA["connections"][0]
        self.assertEqual(validate_connections([]), [])
        with self.assertRaises(ValueError):
            validate_connections([], complete=True)
        for edges in ([edge, edge], [None], [{**edge, "source": "task"}], [{**edge, "extra": True}]):
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                validate_connections(edges)

    def test_viewport_bounds(self):
        value = {**document()["graph"], "viewport": {"x": 20, "y": -1, "zoom": .6}}
        self.assertEqual(validate_graph(value)["viewport"]["zoom"], .6)
        for zoom in (True, 0, 2, "1", None, float("nan")):
            value["viewport"]["zoom"] = zoom
            with self.subTest(zoom=zoom), self.assertRaises(ValueError):
                validate_graph(value)


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = WorkflowStore(self.root / "workflows", self.root / "nreact.toml")

    def test_save_load_list_and_restart(self):
        record = self.store.save({"workflow": document()})
        self.assertEqual(self.store.get(record["id"]), record)
        self.assertEqual(self.store.list()["workflows"][0]["name"], "Read project")
        restarted = WorkflowStore(self.store.directory, self.store.config_path)
        self.assertEqual(restarted.get(record["id"])["workflow"], record["workflow"])
        self.assertFalse(self.store.config_path.exists())

    def test_update_and_delete_require_latest_revision(self):
        first = self.store.save({"workflow": document()})
        value = document()
        value["task"] = "new task"
        second = self.store.save({"workflow": value, "id": first["id"], "revision": first["revision"]})
        self.assertNotEqual(second["revision"], first["revision"])
        for operation in (self.store.save, self.store.delete):
            with self.assertRaises(WorkflowConflict):
                operation({"workflow": value, "id": first["id"], "revision": first["revision"]})
        self.assertEqual(self.store.get(first["id"])["workflow"]["task"], "new task")
        self.store.delete({"id": second["id"], "revision": second["revision"]})
        self.assertEqual(self.store.list()["workflows"], [])

    def test_atomic_failure_preserves_previous_file(self):
        first = self.store.save({"workflow": document()})
        path = self.store.directory / f"{first['id']}.json"
        before = path.read_bytes()
        for name in ("os.replace", "os.fsync"):
            with patch(f"nreact.workflows.{name}", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    self.store.save({"workflow": document(), "id": first["id"], "revision": first["revision"]})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(self.store.directory.glob(".*")), [])

    def test_identifiers_cannot_escape_storage(self):
        for identity in ("../nreact", "A" * 32, "", None, []):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.store.get(identity)

    def test_malformed_records_are_skipped(self):
        record = self.store.save({"workflow": document()})
        for index, value in enumerate((None, [], {}, {**record, "updated_at": "yesterday"})):
            (self.store.directory / f"{index:032x}.json").write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.store.list()["total"], 1)

    def test_record_read_limit(self):
        record = self.store.save({"workflow": document()})
        with patch("nreact.workflows.MAX_RECORD_BYTES", 50):
            with self.assertRaises(ValueError):
                self.store.get(record["id"])

    def test_nonfinite_json_is_rejected(self):
        record = self.store.save({"workflow": document()})
        path = self.store.directory / f"{record['id']}.json"
        raw = path.read_text().replace('"x": 10', '"x": NaN')
        path.write_text(raw)
        with self.assertRaises(ValueError):
            self.store.get(record["id"])

    def test_concurrent_tabs_have_one_successful_revision_update(self):
        record = self.store.save({"workflow": document()})
        results = []
        barrier = threading.Barrier(2)
        def write():
            barrier.wait()
            try:
                self.store.save({"workflow": document(), "id": record["id"], "revision": record["revision"]})
                results.append("saved")
            except WorkflowConflict:
                results.append("conflict")
        threads = [threading.Thread(target=write) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_list_is_bounded_and_reports_truncation(self):
        for index in range(202):
            value = document()
            value["name"] = f"workflow {index}"
            self.store.save({"workflow": value})
        listing = self.store.list()
        self.assertEqual(len(listing["workflows"]), 200)
        self.assertEqual(listing["total"], 202)
        self.assertTrue(listing["truncated"])

    @unittest.skipIf(os.name == "nt", "Windows permissions are ACL-based")
    def test_workflow_file_is_owner_only(self):
        record = self.store.save({"workflow": document()})
        self.assertEqual((self.store.directory / f"{record['id']}.json").stat().st_mode & 0o777, 0o600)

    def test_symlink_does_not_read_outside_store(self):
        self.store.directory.mkdir()
        target = self.root / "elsewhere.json"
        target.write_text("{}")
        link = self.store.directory / f"{'a' * 32}.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.store.get("a" * 32)


class WorkflowHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "nreact.toml"
        self.server = make_server(self.path, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temporary.cleanup()

    def request(self, path, payload=None, *, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Nreact-Token"] = self.server.app.token
        connection.request("GET" if payload is None else "POST", path,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    def test_crud_and_stale_conflict(self):
        status, saved = self.request("/api/workflow", {"workflow": document()})
        self.assertEqual(status, 200)
        status, opened = self.request(f"/api/workflow?id={saved['id']}")
        self.assertEqual(status, 200)
        self.assertIn('name = "fixture"', opened["toml"])
        self.assertEqual(self.request("/api/workflows")[1]["total"], 1)
        self.assertEqual(self.request("/api/workflow", {"workflow": document(), "id": saved["id"], "revision": "old"})[0], 409)
        self.assertEqual(self.request("/api/workflow/delete", {"id": saved["id"], "revision": "old"})[0], 409)
        self.assertEqual(self.request("/api/workflow/delete", {"id": saved["id"], "revision": saved["revision"]})[0], 200)
        self.assertEqual(self.request(f"/api/workflow?id={saved['id']}")[0], 404)

    def test_workflow_endpoints_require_session_token(self):
        for path, payload in (("/api/workflows", None), ("/api/workflow?id=" + "a" * 32, None),
                              ("/api/workflow", {"workflow": document()}),
                              ("/api/workflow/validate", {"workflow": document()}),
                              ("/api/workflow/delete", {"id": "a" * 32})):
            self.assertEqual(self.request(path, payload, token=False)[0], 403)

    def test_import_is_read_only_and_credential_free(self):
        value = document()
        value["config"]["model"].update(api_key_env="PRIVATE", auth="chatgpt", auth_file="private.json")
        with patch("nreact.runs.build_agent") as build:
            status, result = self.request("/api/workflow/validate", {"workflow": value})
        self.assertEqual(status, 200)
        build.assert_not_called()
        self.assertEqual(result["workflow"]["config"]["model"]["api_key_env"], "")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.server.app.workflows.directory.exists())

    def test_export_roundtrips_and_retains_task_and_layout(self):
        status, result = self.request("/api/export", {"kind": "workflow", "workflow": document()})
        self.assertEqual(status, 200)
        self.assertLessEqual(len(result["content"].encode()), 60000)
        loaded = validate_workflow(json.loads(result["content"]))
        self.assertEqual(loaded["graph"]["positions"]["model"], {"x": 10, "y": 20})
        self.assertEqual(loaded["task"], "Read README.md")
        self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), result["content"])

    def test_invalid_graph_and_json_shapes_report_400(self):
        self.assertEqual(self.request("/api/export", {"kind": "graph", "graph": {"version": 2, "positions": [], "connections": []}})[0], 400)
        self.assertEqual(self.request("/api/workflow", {"workflow": []})[0], 400)
        self.assertEqual(self.request("/api/workflow?id=../outside")[0], 400)


if __name__ == "__main__":
    unittest.main()
