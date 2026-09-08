# OpenAI authentication

nreact supports ChatGPT OAuth through the public native-client flow used by OpenAI's open-source Codex client. The `ChatGPTModel` adapter sends ReAct prompts to the ChatGPT Codex Responses endpoint. Account access and available models follow your ChatGPT workspace and plan; see [OpenAI authentication](https://learn.chatgpt.com/docs/auth).

Authentication commands are explicit. Importing nreact, loading TOML and constructing a model do not start a login or read an existing credential cache.

## Sign in

Run this when you are ready to sign in yourself:

```sh
uv run nreact auth login
```

The command opens OpenAI's authorization page and waits for a callback on `http://localhost:1455/auth/callback`. It uses a random state and PKCE S256, exchanges the returned code over HTTPS, and saves the tokens only after the exchange succeeds. The callback listener binds to loopback and closes when login finishes, times out or is interrupted.

To open the URL manually:

```sh
uv run nreact auth login --no-browser
```

For a remote terminal or an occupied callback port:

```sh
uv run nreact auth login --device
```

Device login prints OpenAI's verification URL and a one-time code, then polls for authorization. It requires device-code login to be available in your ChatGPT security or workspace settings. The command does not open a browser in device mode. `--device-auth` is an alias for `--device`; `--timeout` accepts 30–900 seconds and defaults to 900.

## Select the model adapter

Choose a Codex model available to your account and configure:

```toml
[model]
auth = "chatgpt"
name = "your-codex-model"
timeout = 180
send_stop = true
```

The existing agent and tool settings still apply. A command-line override is also available:

```sh
uv run nreact run "Explain the ReAct loop." --auth chatgpt --model your-codex-model
```

`NREACT_AUTH=chatgpt` supplies the authentication mode when `model.auth` is omitted. The default remains `api_key`, which uses the existing OpenAI-compatible Chat Completions adapter and its API-key/environment settings.

For Python applications:

```python
from nreact import Agent, ChatGPTModel, ToolEnvironment

model = ChatGPTModel("your-codex-model", timeout=180)
agent = Agent(model, ToolEnvironment([]))
result = agent.run("Explain the ReAct loop.")
```

OAuth tokens are sent only to the fixed `https://chatgpt.com/backend-api/codex/responses` endpoint. `model.base_url`, API-key fields, `max_tokens` and `temperature` apply to the Chat Completions adapter. The ChatGPT adapter follows the Codex Responses request format and server generation settings; `max_tokens` does not impose an output-token or spending limit on ChatGPT calls. Agent step and character limits still apply.

With ChatGPT, `send_stop = true` truncates the completed text at the first ReAct stop marker locally. The complete server response is consumed and its reported usage is retained. `--no-stop` disables this local truncation. The adapter uses text output for the existing ReAct controller; nreact tools continue to run through its own environment.

## Status and sign out

```sh
uv run nreact auth status
uv run nreact auth status --json
uv run nreact auth logout
```

Status inspects only the local cache and reports `signed_out`, `cached` or `refresh_required`. It does not validate the account with OpenAI, refresh tokens, or print token/account values. Its exit code is 1 when signed out and 0 when credentials are cached. Logout removes nreact's cached credentials and leaves other applications' sessions alone. It does not revoke the OpenAI grant remotely. Logout also supports `--json`.

## Credential storage

The default cache is `~/.nreact/openai-auth.json`, separate from Codex's cache. Set `NREACT_AUTH_FILE` or pass `--auth-file` to select another nreact cache. Authentication commands resolve relative paths from the current directory:

```sh
uv run nreact auth login --auth-file ~/.nreact/work-openai-auth.json
uv run nreact auth status --auth-file ~/.nreact/work-openai-auth.json
```

For agent runs, `model.auth_file` selects a cache relative to the TOML file's directory, with `~` expansion. An explicit `run` or `eval` `--auth-file` is relative to the shell's current directory. When the TOML field is empty, `NREACT_AUTH_FILE` or the default path applies. Keep credential caches outside the project and use the same path for login and execution.

On Windows, the cache payload is encrypted with DPAPI for the current Windows user. On POSIX systems, it uses a mode-0600 file; reading a cache accessible to the group or other users is rejected. Writes use a temporary file and atomic replacement. Credential-file and lock-file symlinks are rejected.

Access tokens refresh before expiry. A file lock serializes rotating-token refresh between nreact processes. An HTTP 401 allows one credential refresh and request retry; other HTTP failures, connection failures and interrupted streams are returned to the agent. A concurrent change to another ChatGPT account aborts unauthorized recovery. Tokens are absent from TOML, model metadata, CLI status and run traces. Task and tool content still follows the normal trace behavior.

The OAuth client identifier, authorization/token routes, device-code protocol and Responses endpoint follow [OpenAI Codex source at ef6c0582](https://github.com/openai/codex/tree/ef6c0582028ff8e1228e6031465c1c2d9d5d51b6/codex-rs/login/src). The implementation uses Python's standard library and does not launch or import the Codex application. The FedRAMP ChatGPT endpoint is currently unsupported.
