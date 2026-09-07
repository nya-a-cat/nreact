"""ReAct's Search and Lookup actions over Wikipedia or an offline page mapping."""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from difflib import get_close_matches

from .types import Observation


def sentences(page: str) -> list[str]:
    """Simple period-space segmentation matching the reference environment."""
    return [part.strip().rstrip(".") + "." for paragraph in page.splitlines()
            for part in paragraph.split(". ") if part.strip()]


class WikiEnvironment:
    instructions = (
        "Search[entity]: Open an exact Wikipedia title and read its first five sentences. "
        "Missing titles return up to five suggestions.\n"
        "Lookup[keyword]: Read the next sentence containing keyword in the current page. "
        "Repeat to advance; a new keyword starts a new lookup."
    )

    def __init__(self, *, pages: Mapping[str, str] | None = None, timeout: float = 20,
                 fetch_json: Callable[[dict], dict] | None = None):
        if timeout <= 0:
            raise ValueError("Timeout must be positive.")
        self.pages = dict(pages) if pages is not None else None
        self.timeout = timeout
        self._fetch_json = fetch_json or self._request
        self.reset()

    def reset(self) -> None:
        self.page: str | None = None
        self.source = ""
        self.keyword: str | None = None
        self.matches: list[str] = []
        self.cursor = 0

    def _request(self, parameters: dict) -> dict:
        parameters = {"action": "query", "format": "json", "formatversion": 2, **parameters}
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(url, headers={
            "User-Agent": "nreact/0.2.0 (https://github.com/nya-a-cat/nreact)",
        })
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError("Wikipedia response exceeded 4 MB.")
        parsed = json.loads(data)
        if "error" in parsed:
            raise ValueError("Wikipedia API error.")
        return parsed

    def search(self, entity: str) -> str:
        self.reset()
        if not entity.strip():
            return "Supply a non-empty page title."
        if self.pages is not None:
            title = next((key for key in self.pages if key.casefold() == entity.casefold()), None)
            if title is None:
                suggestions = get_close_matches(entity, self.pages, n=5, cutoff=0)
                return f"Could not find {entity}. Similar: {suggestions}."
            self.page, self.source = self.pages[title], f"offline:{title}"
        else:
            data = self._fetch_json({"prop": "extracts|pageprops", "explaintext": 1,
                                     "redirects": 1, "titles": entity})
            pages = data["query"]["pages"]
            page = pages[0] if pages else {}
            if "missing" in page or not page.get("extract") or "disambiguation" in page.get("pageprops", {}):
                suggestions = self._fetch_json({"list": "search", "srsearch": entity,
                                                 "srwhat": "title", "srlimit": 5})
                titles = [item["title"] for item in suggestions["query"]["search"]][:5]
                return f"Could not find {entity}. Similar: {titles}."
            self.page = page["extract"]
            self.source = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page["title"].replace(" ", "_"))
        return " ".join(sentences(self.page)[:5]) + f"\nSource: {self.source}"

    def lookup(self, keyword: str) -> str:
        if self.page is None:
            return "No current page. Use Search[entity] first."
        if not keyword.strip():
            return "Supply a non-empty keyword."
        if keyword != self.keyword:
            self.keyword = keyword
            self.matches = [text for text in sentences(self.page) if keyword.lower() in text.lower()]
            self.cursor = 0
        if self.cursor >= len(self.matches):
            return "No more results."
        text = self.matches[self.cursor]
        self.cursor += 1
        return f"(Result {self.cursor} / {len(self.matches)}) {text}\nSource: {self.source}"

    def step(self, name: str, argument: str) -> Observation:
        if name == "search":
            return Observation(self.search(argument))
        if name == "lookup":
            return Observation(self.lookup(argument))
        return Observation("Unknown action. Use Search[entity], Lookup[keyword], or Finish[answer].")
