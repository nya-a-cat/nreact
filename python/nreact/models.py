"""Text-generation adapters. API credentials are never stored in traces."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from .types import Completion


class ModelError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatModel:
    """A synchronous OpenAI-compatible /chat/completions adapter.

    Supports servers such as llama.cpp. Select a model explicitly. Retries are
    delegated to the caller so a single episode has a predictable call budget.
    """

    def __init__(
        self, model: str, *, base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str | None = None, timeout: float = 60,
        max_tokens: int = 512, temperature: float = 0, send_stop: bool = True,
    ):
        url = urllib.parse.urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("Use an HTTP(S) API base URL without credentials, query or fragment.")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote API endpoints require HTTPS.")
        if not model.strip() or timeout <= 0 or max_tokens < 1:
            raise ValueError("Set a model and positive timeout/max_tokens.")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.send_stop = send_stop

    def trace_metadata(self) -> dict:
        return {"adapter": "chat_completions", "model": self.model,
                "base_url": self.base_url, "max_tokens": self.max_tokens,
                "temperature": self.temperature, "timeout": self.timeout,
                "send_stop": self.send_stop}

    @classmethod
    def from_env(cls, model: str | None = None, **kwargs) -> "ChatModel":
        selected = model or os.getenv("NREACT_MODEL")
        if not selected:
            raise ValueError("Set NREACT_MODEL or pass --model.")
        return cls(selected, base_url=os.getenv("NREACT_BASE_URL", "http://127.0.0.1:8080/v1"),
                   api_key=os.getenv("NREACT_API_KEY"), **kwargs)

    def generate(self, prompt: str, *, stop: Sequence[str]) -> Completion:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Follow the supplied ReAct text protocol. Generate one turn."},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature, "max_tokens": self.max_tokens,
        }
        if self.send_stop:
            body["stop"] = list(stop)
        headers = {"Content-Type": "application/json", "User-Agent": "nreact/0.1.0"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(self.base_url + "/chat/completions",
                                         data=json.dumps(body).encode(), headers=headers)
        handlers = [_NoRedirect()]
        if urllib.parse.urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(urllib.request.ProxyHandler({}))
        try:
            with urllib.request.build_opener(*handlers).open(request, timeout=self.timeout) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ModelError("API response exceeded 4 MB.")
                data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise ModelError("API returned no text content.")
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                raise ModelError("API returned invalid usage.")
            return Completion(text, {k: v for k, v in usage.items() if isinstance(v, int)})
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise ModelError(f"API returned HTTP {code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelError("API connection failed or timed out.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("API returned an invalid chat completion.") from None


class ScriptedModel:
    """An explicit offline fixture for testing and examples; performs no inference."""

    def __init__(self, responses: Sequence[str]):
        self._responses = iter(responses)

    def generate(self, prompt: str, *, stop: Sequence[str]) -> Completion:
        return Completion(next(self._responses))
