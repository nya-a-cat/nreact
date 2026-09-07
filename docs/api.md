# API

## Agent

`Agent(model, environment, *, mode="dense", examples="", max_steps=20, max_context_chars=100_000, max_observation_chars=12_000)`

`run(task, *, trace_path=None, on_event=None) -> Result` starts a fresh episode and resets its environment. `trace_path` exclusively creates a JSONL file; `on_event` receives an `Event` synchronously. Callback failures propagate to the caller and close the trace. Runs do not implicitly retry, resume or reuse prior task history.

The context limit counts Python characters. It is a configurable upper bound, not a tokenizer-based model context guarantee. Context exhaustion stops the run before the next API request. Long observations are truncated with a visible marker; choose a higher observation limit for reproductions requiring full text. Custom tools execute synchronously with the caller's permissions; implement their network timeouts and process isolation in the tool itself.

## Result

- `status`: `finished`, `environment_done`, `max_steps`, `context_limit`, or `model_error`.
- `answer`: the model's finish argument or the terminal environment answer; otherwise `None`.
- `steps`: generations processed by the protocol, including invalid and thought-only turns.
- `model_calls`: attempted calls, including failed calls.
- `usage`: sums of integer usage fields actually returned by the provider. Missing usage is an empty dictionary.
- `events`: ordered `thought`, `action`, `observation`, `protocol_error` and `error` events.
- `elapsed_seconds`, `error`, `reward`: timing, sanitized model error and last non-null environment reward.

`finished` means that the model submitted an answer. Answer correctness is determined by your application or evaluator. A stop at a limit leaves `answer=None`.

## Custom models

```python
from nreact import Completion

class MyModel:
    def generate(self, prompt: str, *, stop):
        text = my_generation_client(prompt=prompt, stop=stop)
        return Completion(text, usage={})
```

The returned value can also be a string. Generated output must use the current turn number or unnumbered labels. Tool arguments are one line; JSON arguments may contain escaped newlines and nested arrays.

Dense example:

```text
Thought 1: Find the requested record.
Action 1: Search[record title]
```

Sparse examples, each representing one model call:

```text
Thought 1: First inspect the room.
```

```text
Action 2: Move[kitchen]
```

The `Think[text]` action is also accepted in sparse mode. Model-generated `Observation:` lines and multiple action lines cause a protocol error before tool execution. Malformed output consumes a step and supplies a corrective protocol observation on the next call.

`ChatModel` sends `model`, `messages`, `temperature`, `max_tokens` and optional `stop` to `/chat/completions`. It uses a bounded HTTP read, disables redirects and bypasses proxies for loopback servers. It accepts textual `message.content`. Configure an adapter for providers with different request fields. Exceptions suppress response bodies and credential-bearing URL details.

## Custom environments

```python
from nreact import Observation

class Room:
    instructions = "Move[room]: Move to an adjacent room."

    def reset(self):
        self.room = "hall"

    def step(self, name, argument):
        if name != "move":
            return Observation("Use Move[room].")
        self.room = argument
        return Observation("Now in " + self.room, done=self.room == "goal",
                           answer="Reached goal" if self.room == "goal" else None)
```

`Finish` is handled by the controller and is never dispatched. Thought-only turns likewise leave the environment untouched. Environment exceptions become observations containing the exception class only. Errors during `reset()` propagate because an episode cannot proceed without an initialized environment.

`workspace_tools` resolves paths within a chosen directory and rejects traversal and symlinks pointing outside it. It is intended for trusted local files. Filesystem races, untrusted custom Python tools and prompt injection require application-level protection. Readable secrets inside the selected root remain readable; choose the root accordingly.

## Trace format

Each JSONL file contains a `start` record with schema version, configuration and prompt hash, `generation` records with raw model outputs, ordered `event` records, and a `result` record. The built-in model adapter includes endpoint and inference settings, excluding the API key. Records are flushed as they happen so a terminated process leaves its completed steps available. An interrupted or callback-failed run can have no final result record. Task/tool text is stored verbatim and can contain sensitive data.
