import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact import ScriptedModel
from nreact.config import (
    ConfiguredEnvironment, build_agent, dumps_config, load_config, parse_config,
    revision, save_config, tomllib,
)


class ConfigTests(unittest.TestCase):
    def test_toml_roundtrip_and_escaping(self):
        data = {
            "ui": {"theme": "graphite"},
            "model": {"name": "test", "api_key": 'test-"key"-only'},
            "tools": {"wikipedia": False, "workspace": "C:\\资料\\workspace",
                      "custom": [{"name": "stock", "description": 'Look up "stock".', "callable": "my_tools:stock"}]},
        }
        config = parse_config(data, use_environment=False)
        self.assertEqual(parse_config(tomllib.loads(dumps_config(config)), use_environment=False).to_dict(), config.to_dict())
        self.assertNotIn('test-"key"-only', repr(config))
        self.assertNotIn("api_key", config.public_dict()["model"])
        self.assertEqual(config.public_dict()["ui"]["theme"], "graphite")
        self.assertEqual(parse_config({}, use_environment=False).ui.theme, "classic")

    def test_environment_fallback_and_file_precedence(self):
        with patch.dict(os.environ, {"NREACT_MODEL": "env-model", "NREACT_BASE_URL": "https://env.example/v1"}):
            default = parse_config({})
            self.assertEqual(default.model.name, "env-model")
            config = parse_config({"model": {"name": "file-model", "base_url": "https://file.example/v1"}})
            self.assertEqual(config.model.name, "file-model")
            self.assertEqual(config.model.base_url, "https://file.example/v1")

    def test_api_key_precedence(self):
        with patch.dict(os.environ, {"MY_KEY": "env-key"}):
            config = parse_config({"model": {"api_key_env": "MY_KEY"}}, use_environment=False)
            self.assertEqual(config.api_key(), "env-key")
            config.model.api_key = "file-key"
            self.assertEqual(config.api_key(), "file-key")

    def test_invalid_configuration(self):
        cases = [
            {"unknown": {}}, {"model": {"unknown": True}}, {"model": {"timeout": True}},
            {"ui": "graphite"}, {"ui": {"unknown": True}}, {"ui": {"theme": "missing"}},
            {"ui": {"theme": []}}, {"ui": {"theme": ""}},
            {"model": {"max_tokens": 0}}, {"model": {"api_key": "key\r\nheader"}},
            {"model": {"temperature": float("nan")}}, {"model": {"base_url": "http://remote.example/v1"}},
            {"agent": {"mode": "other"}}, {"agent": {"mode": []}}, {"agent": {"max_steps": 0}},
            {"tools": {"wikipedia": "yes"}}, {"tools": {"workspace": 5}},
            {"tools": {"custom": [{"name": "x"}]}},
            {"tools": {"custom": [{"name": "lookup", "description": "x", "callable": "my_tools:lookup"}]}},
            {"tools": {"custom": [{"name": "x", "description": "x", "callable": "../file.py:run"}]}},
            {"tools": {"wikipedia": False}, "agent": {"paper": "hotpotqa"}},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_config(data, use_environment=False)

    def test_init_does_not_overwrite_and_revision_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new" / "nested" / "nreact.toml"
            config = parse_config({}, path, use_environment=False)
            self.assertEqual(revision(path), "missing")
            save_config(config)
            before = revision(path)
            with self.assertRaises(FileExistsError):
                save_config(config)
            config.model.name = "new-model"
            save_config(config, overwrite=True)
            self.assertNotEqual(revision(path), before)
            self.assertEqual(load_config(path).model.name, "new-model")
            self.assertEqual(list(path.parent.glob(".nreact-*.tmp")), [])
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_malformed_toml_and_size_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[model]\napi_key = "secret\n', encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                load_config(path)
            self.assertNotIn("secret", str(error.exception))
            path.write_text("#" * 65_537, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)
            with self.assertRaises(FileNotFoundError):
                load_config(Path(directory) / "missing.toml")

    def test_failed_first_save_leaves_no_partial_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nreact.toml"
            config = parse_config({}, path, use_environment=False)
            fdopen = os.fdopen

            def failing_writer(*args, **kwargs):
                writer = fdopen(*args, **kwargs)
                write = writer.write

                def fail(text):
                    write(text[:15])
                    writer.flush()
                    raise OSError("disk full")

                writer.write = fail
                return writer

            with patch("nreact.config.os.fdopen", side_effect=failing_writer):
                with self.assertRaises(OSError):
                    save_config(config)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob(".nreact-*.tmp")), [])
            save_config(config)
            self.assertEqual(load_config(path).to_dict(), config.to_dict())

    def test_sync_failure_preserves_missing_or_previous_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nreact.toml"
            config = parse_config({}, path, use_environment=False)
            for overwrite in (False, True):
                if overwrite:
                    save_config(config)
                before = path.read_bytes() if path.exists() else None
                with self.subTest(overwrite=overwrite), patch("nreact.config.os.fsync", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        save_config(config, overwrite=overwrite)
                self.assertEqual(path.read_bytes() if path.exists() else None, before)
                self.assertEqual(list(path.parent.glob(".nreact-*.tmp")), [])

    def test_initial_publish_does_not_replace_a_concurrent_creator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nreact.toml"
            config = parse_config({}, path, use_environment=False)
            newer = b'[model]\nname = "concurrent"\n'

            def create_before_publish(descriptor):
                self.assertFalse(path.exists())
                path.write_bytes(newer)

            with patch("nreact.config.os.fsync", side_effect=create_before_publish):
                with self.assertRaises(FileExistsError):
                    save_config(config)
            self.assertEqual(path.read_bytes(), newer)
            self.assertEqual(list(path.parent.glob(".nreact-*.tmp")), [])

    def test_local_tool_loads_only_at_runtime_and_uses_config_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "imported.txt"
            (root / "my_tools.py").write_text(
                "from pathlib import Path\nPath(__file__).with_name('imported.txt').write_text('yes')\n"
                "def stock(name):\n    return '12'\n", encoding="utf-8")
            config = parse_config({"model": {"name": "fixture"}, "tools": {"wikipedia": False,
                "custom": [{"name": "stock", "description": "Read stock", "callable": "my_tools:stock"}]}}, root / "nreact.toml")
            save_config(config)
            config = load_config(config.path)
            self.assertFalse(marker.exists())
            with patch("nreact.config.ChatModel") as adapter:
                adapter.return_value = ScriptedModel(["Thought: Query\nAction: Stock[x]", "Thought: Found 12\nAction: Finish[12]"])
                result = build_agent(config).run("Check stock")
            self.assertTrue(marker.exists())
            self.assertEqual(result.answer, "12")

    def test_combined_workspace_and_wiki_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "note.txt").write_text("blue", encoding="utf-8")
            config = parse_config({"tools": {"workspace": "."}}, path / "nreact.toml")
            environment = ConfiguredEnvironment(config)
            self.assertIn("Search", environment.instructions)
            self.assertIn("read[", environment.instructions)
            self.assertEqual(environment.step("read", "note.txt").text, "blue")

    def test_cli_init_and_missing_explicit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nreact.toml"
            init = subprocess.run([sys.executable, "-m", "nreact", "init", "--config", str(path)], capture_output=True, text=True, timeout=20)
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertEqual(load_config(path).agent.mode, "dense")
            duplicate = subprocess.run([sys.executable, "-m", "nreact", "init", "--config", str(path)], capture_output=True, text=True, timeout=20)
            self.assertEqual(duplicate.returncode, 2)
            missing = subprocess.run([sys.executable, "-m", "nreact", "run", "Task", "--config", str(Path(directory) / "missing.toml")], capture_output=True, text=True, timeout=20)
            self.assertEqual(missing.returncode, 2)
