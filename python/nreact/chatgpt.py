"""Text-only ChatGPT Codex Responses transport for nreact's ReAct loop."""

import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from http.client import HTTPException
from pathlib import Path

from ._credentials import AuthError
from .auth import AuthStore
from .models import ModelError, _NoRedirect
from .types import Completion

BASE_URL = "https://chatgpt.com/backend-api/codex"


def _events(response):
    """Decode bounded SSE records, including a final event without a blank line."""
    record: list[bytes] = []
    consumed = 0
    while True:
        raw = response.readline(4_000_001)
        consumed += len(raw)
        if len(raw) > 4_000_000 or consumed > 16_000_000:
            raise ModelError("ChatGPT response exceeded the stream size limit.")
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            record.append(line[5:].removeprefix(b" "))
        if not line and record:
            data = b"\n".join(record)
            record.clear()
            if data == b"[DONE]":
                return
            event = json.loads(data)
            if not isinstance(event, dict):
                raise ModelError("ChatGPT returned an invalid stream event.")
            yield event
        if not raw:
            return


def _output_text(output) -> str:
    if not isinstance(output, list):
        return ""
    texts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ModelError("ChatGPT returned an invalid output message.")
        for part in content:
            if not isinstance(part, dict):
                raise ModelError("ChatGPT returned an invalid output part.")
            if part.get("type") == "refusal":
                raise ModelError("ChatGPT declined this request.")
            if part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise ModelError("ChatGPT returned an invalid text output.")
                texts.append(part["text"])
    return "\n".join(texts)


def _completion(response) -> Completion:
    deltas: list[str] = []
    finished_items: dict[int, dict] = {}
    for event in _events(response):
        kind = event.get("type")
        if kind in {"error", "response.failed", "response.incomplete"}:
            raise ModelError("ChatGPT did not complete the response.")
        if kind in {"response.refusal.delta", "response.refusal.done"}:
            raise ModelError("ChatGPT declined this request.")
        if kind == "response.output_text.delta":
            if not isinstance(event.get("delta"), str):
                raise ModelError("ChatGPT returned an invalid text delta.")
            deltas.append(event["delta"])
        elif kind == "response.output_item.done":
            index, item = event.get("output_index"), event.get("item")
            if type(index) is int and index >= 0 and isinstance(item, dict):
                finished_items[index] = item
        elif kind == "response.completed":
            completed = event.get("response")
            if not isinstance(completed, dict) or completed.get("status", "completed") != "completed":
                raise ModelError("ChatGPT returned an invalid completed response.")
            text = (_output_text(completed.get("output"))
                    or _output_text([finished_items[key] for key in sorted(finished_items)])
                    or "".join(deltas))
            if not text:
                raise ModelError("ChatGPT returned no text content.")
            raw_usage = completed.get("usage") or {}
            if not isinstance(raw_usage, dict):
                raise ModelError("ChatGPT returned invalid token usage.")
            usage = {}
            for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                                   ("total_tokens", "total_tokens")):
                value = raw_usage.get(source)
                if type(value) is int and value >= 0:
                    usage[target] = value
            details = raw_usage.get("input_tokens_details")
            if isinstance(details, dict) and type(details.get("cached_tokens")) is int and details["cached_tokens"] >= 0:
                usage["cached_prompt_tokens"] = details["cached_tokens"]
            return Completion(text, usage)
    raise ModelError("ChatGPT stream ended before response.completed.")


class ChatGPTModel:
    """Call the fixed Codex Responses endpoint with nreact-owned OAuth tokens.

    Tokens refresh before expiry. An HTTP 401 permits one refresh/retry; other
    failed requests and interrupted streams are returned to the caller.
    Generation limits and temperature follow the server's Codex model settings.
    Stop markers are applied locally after the response completes, preserving
    the server's full token-usage accounting.
    """

    def __init__(self, model: str, *, auth_file: str | Path | None = None,
                 timeout: float = 60, stop_locally: bool = True):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Select a ChatGPT Codex model explicitly.")
        try:
            valid_timeout = type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("Set a positive timeout.")
        if type(stop_locally) is not bool:
            raise ValueError("stop_locally must be a boolean.")
        self.model = model
        self.timeout = timeout
        self.stop_locally = stop_locally
        self._auth = AuthStore(auth_file)

    def trace_metadata(self) -> dict:
        return {"adapter": "chatgpt_codex_responses", "model": self.model,
                "base_url": BASE_URL, "timeout": self.timeout,
                "stop_mode": "local" if self.stop_locally else "none"}

    def generate(self, prompt: str, *, stop: Sequence[str]) -> Completion:
        body = json.dumps({
            "model": self.model,
            "instructions": "Follow the supplied ReAct text protocol. Generate exactly one turn. Do not generate an Observation.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
            "store": False, "stream": True,
        }).encode("utf-8")
        try:
            tokens = self._auth.tokens()
            for attempt in range(2):
                request = urllib.request.Request(BASE_URL + "/responses", data=body, headers={
                    "Authorization": "Bearer " + tokens.access_token,
                    "ChatGPT-Account-Id": tokens.account_id,
                    "Content-Type": "application/json", "Accept": "text/event-stream",
                    "User-Agent": "nreact/0.3.0", "originator": "nreact",
                })
                try:
                    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                        result = _completion(response)
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    exc.close()
                    if status == 401 and attempt == 0:
                        tokens = self._auth.tokens(rejected_access_token=tokens.access_token,
                                                   expected_account_id=tokens.account_id)
                        continue
                    raise ModelError(f"ChatGPT returned HTTP {status}.") from None
                text = result.text
                if self.stop_locally:
                    positions = [text.find(marker) for marker in stop if marker and marker in text]
                    if positions:
                        text = text[:min(positions)]
                return Completion(text, result.usage)
        except AuthError as exc:
            raise ModelError(str(exc)) from None
        except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
            raise ModelError("ChatGPT connection failed or timed out.") from None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ModelError("ChatGPT returned an invalid Responses stream.") from None
        raise ModelError("ChatGPT authentication was rejected after refreshing credentials.")
