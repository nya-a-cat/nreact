import tempfile
import unittest
from pathlib import Path

from nreact import Tool, ToolEnvironment, workspace_tools
from nreact.wiki import WikiEnvironment


class EnvironmentTests(unittest.TestCase):
    def test_search_first_five_and_lookup_cursor(self):
        wiki = WikiEnvironment(pages={"A": "One. Two. Three. Four. Five. Six match. Seven match."})
        result = wiki.step("search", "A").text
        self.assertIn("Five.", result)
        self.assertNotIn("Six", result)
        self.assertIn("(Result 1 / 2) Six match.", wiki.lookup("match"))
        self.assertIn("(Result 2 / 2) Seven match.", wiki.lookup("match"))
        self.assertEqual(wiki.lookup("match"), "No more results.")
        self.assertIn("One.", wiki.lookup("one"))
        self.assertIn("(Result 1 / 2)", wiki.lookup("match"))

    def test_missing_search_clears_page(self):
        wiki = WikiEnvironment(pages={"A": "One."})
        wiki.search("A")
        self.assertIn("Similar", wiki.search("missing"))
        self.assertIn("No current page", wiki.lookup("One"))

    def test_api_extract_and_suggestions(self):
        calls = []
        def fetch(params):
            calls.append(params)
            if params.get("titles") == "Known":
                return {"query": {"pages": [{"title": "Known", "extract": "Known page. More text."}]}}
            if "titles" in params:
                return {"query": {"pages": [{"missing": True}]}}
            return {"query": {"search": [{"title": "Known"}]}}
        wiki = WikiEnvironment(fetch_json=fetch)
        self.assertIn("https://en.wikipedia.org/wiki/Known", wiki.search("Known"))
        self.assertIn("Similar: ['Known']", wiki.search("Unknown"))
        self.assertEqual(len(calls), 3)

    def test_disambiguation(self):
        def fetch(params):
            if "titles" in params:
                return {"query": {"pages": [{"extract": "May refer to.", "pageprops": {"disambiguation": ""}}]}}
            return {"query": {"search": []}}
        self.assertIn("Similar: []", WikiEnvironment(fetch_json=fetch).search("Ambiguous"))

    def test_tool_names(self):
        for name in ["finish", "think", "bad-name", "", "1tool"]:
            with self.assertRaises(ValueError):
                ToolEnvironment([Tool(name, "", str)])
        with self.assertRaises(ValueError):
            ToolEnvironment([Tool("A", "", str), Tool("a", "", str)])

    def test_workspace_read_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "hello.txt").write_text("你好", encoding="utf-8")
            (Path(directory) / "outside.txt").write_text("private", encoding="utf-8")
            env = ToolEnvironment(workspace_tools(root))
            self.assertEqual(env.step("read", "hello.txt").text, "你好")
            self.assertIn("hello.txt", env.step("list", ".").text)
            for path in ["../outside.txt", str(Path(directory) / "outside.txt")]:
                with self.assertRaises(ValueError):
                    env.step("read", path)

    def test_workspace_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "large.txt").write_text("123456789", encoding="utf-8")
            env = ToolEnvironment(workspace_tools(directory, max_bytes=3))
            with self.assertRaises(ValueError):
                env.step("read", "large.txt")

    def test_workspace_external_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            target = Path(directory) / "outside.txt"
            target.write_text("private", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(target)
            except OSError:
                self.skipTest("Symlinks unavailable for current OS permissions.")
            with self.assertRaises(ValueError):
                ToolEnvironment(workspace_tools(root)).step("read", "link.txt")
