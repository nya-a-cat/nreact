# Usage guide

Use `nreact init` to create a TOML file. See [configuration](configuration.md) for model/tool settings and precedence. The environment-variable examples below also work when the corresponding fields are omitted from a TOML file.

Configure a text-generating model served through an OpenAI-compatible Chat Completions endpoint, such as a running [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server). The default endpoint is `http://127.0.0.1:8080/v1`. Remote endpoints require HTTPS.

For ChatGPT subscription authentication, use the [OpenAI OAuth guide](authentication.md). It provides explicit login/status/logout commands and `--auth chatgpt` for `run` and `eval`.

PowerShell:

```powershell
$env:NREACT_MODEL = "your-model-name"
$env:NREACT_BASE_URL = "http://127.0.0.1:8080/v1"
uv run nreact run "Who wrote Pride and Prejudice?" --paper hotpotqa
```

POSIX shells:

```sh
export NREACT_MODEL=your-model-name
export NREACT_BASE_URL=http://127.0.0.1:8080/v1
uv run nreact run "Who wrote Pride and Prejudice?" --paper hotpotqa
```

Set `NREACT_API_KEY` through your environment if the server requires authentication. API usage follows your provider's billing. Credentials are read only when creating `ChatModel` and are excluded from episode configuration.

Read a selected local workspace:

```sh
uv run nreact run "Read README.md and summarize how to run this project." --workspace .
```

This enables `read[path]` and `list[path]`. File contents are sent to the model endpoint you configure. Select a directory containing the files you want the agent to access. These tools read regular UTF-8 files with a 64 KB limit; they do not execute commands or modify files.

Choose sparse reasoning, save a trace, or consume a machine-readable result:

```sh
uv run nreact run "Who designed the analytical engine?" --mode sparse --max-steps 12
uv run nreact run "Who wrote Emma?" --trace episode.jsonl --json
```

Trace paths must be new files. Parent directories must already exist. Traces include tasks, generated thoughts, actions, observations, token usage reported by the provider and the final status. They can contain private task data; `runs/` is ignored by Git.

`--max-tokens` sets the requested output limit for the Chat Completions adapter. `--timeout` configures network timeouts and `--max-steps` caps agent turns. ChatGPT OAuth uses the server's Codex generation settings and does not send `max_tokens` or `temperature`. `--no-stop` omits server stop sequences for Chat Completions and disables local stop-marker truncation for ChatGPT. Use a model that emits visible text in the requested ReAct protocol. Native function-call tool formats require a custom model adapter.
