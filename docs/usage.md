# Usage guide

Configure a text-generating model served through an OpenAI-compatible Chat Completions endpoint, such as a running [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server). The default endpoint is `http://127.0.0.1:8080/v1`. Remote endpoints require HTTPS.

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

`--max-tokens`, `--timeout`, and `--max-steps` bound each generation and episode. Servers that reject stop sequences can use `--no-stop`. Use a model that emits visible text in the requested protocol. Provider-specific hidden reasoning and native function-call formats require a custom model adapter.

