# Configuration

nreact supports a local TOML file, a Vue web interface, environment variables and Python objects.

## Web interface

```sh
uv run nreact ui
```

Open the displayed local URL, enter the model name and API base URL, select tools, and click **Save configuration**. Use **Run agent** to try the saved settings. API calls follow your provider's billing. The latest run stays in server memory until the server exits or another run starts.

The interface edits `nreact.toml` in the current directory. To choose a different file or port:

```sh
uv run nreact ui --config my-agent.toml --port 8766
```

`--no-browser` starts the server without opening a browser. The server binds to `127.0.0.1` and is intended for local use. It uses session tokens and same-origin checks. Node.js is only needed to develop the frontend.

## TOML file

Create an initial file with `uv run nreact init`, or write one yourself:

```toml
[model]
name = "your-model-name"
base_url = "https://api.example.com/v1"
api_key_env = "NREACT_API_KEY"
max_tokens = 512
temperature = 0.0
timeout = 60
send_stop = true

[agent]
mode = "dense"
max_steps = 20
paper = ""

[tools]
wikipedia = true
workspace = "."
```

Then run:

```sh
uv run nreact run "Read README.md and summarize the project."
```

`run` and `eval` automatically read `./nreact.toml` when it exists. An explicitly supplied `--config` must exist. Precedence is **explicit CLI options → TOML fields → environment fallbacks → defaults**. `NREACT_MODEL` and `NREACT_BASE_URL` fill omitted model fields. `agent.paper` accepts `""`, `"hotpotqa"` or `"fever"`; paper examples require Wikipedia tools. `--paper none` disables a file's selected examples.

`agent.max_context_chars` and `agent.max_observation_chars` default to 100000 and 12000. The API guide describes their limits. Unknown fields and invalid types are rejected.

Workspace paths are relative to the TOML file's directory. An explicit CLI `--workspace` is relative to the current working directory and selects local file tools instead of Wikipedia. Within TOML, Wikipedia and workspace tools can be enabled together.

## API keys

Use `model.api_key_env` to reference an environment variable, or save `model.api_key` directly in your local file. A saved key takes priority over the named environment variable. The UI can replace or remove a saved key and never returns its value to the browser. Leaving the password field blank preserves the saved value.

`nreact.toml` is ignored by this repository's Git configuration. Keep other credential-bearing TOML files out of version control too. Saved keys are local plaintext; POSIX writes use mode 0600, and Windows uses the directory's access controls. Clearing a saved key allows the configured environment variable to supply a key again.

Web saves regenerate the supported TOML fields with atomic replacement. Comments and original formatting are not retained. A file changed since the page loaded triggers a conflict; reload before saving again. Configuration errors do not overwrite the existing file. `nreact init` never overwrites an existing file.

## Custom tools

Put a standalone `my_tools.py` beside your TOML file:

```python
def stock(product: str) -> str:
    inventory = {"notebook": 12, "pencil": 40}
    return str(inventory.get(product, 0))
```

Register the function in the TOML file, or use **Add tool** in the interface:

```toml
[[tools.custom]]
name = "stock"
description = "Return available units for a product name."
callable = "my_tools:stock"
```

Handlers use `module:function` syntax. nreact loads standalone Python files relative to the configuration directory, or imports an installed module. Standalone files can import installed packages; use an installed package for tools that need package-relative imports. Importing and running these functions executes trusted Python code with your process's permissions. Loading or saving configuration does not execute them.

Functions receive a string and return a string. Names must be unique and cannot overlap enabled built-in tools. `think` and `finish` are always reserved. Disable Wikipedia if a custom tool needs the name `search` or `lookup`.

## Python API

Use the same configuration in an application:

```python
from nreact.config import build_agent, load_config

agent = build_agent(load_config("nreact.toml"))
result = agent.run("How many notebooks are in stock?")
print(result.answer)
```

Direct construction with `Agent(...)` and `ChatModel.from_env()` remains available and does not automatically load TOML.

## Frontend development

Start the Python backend with `uv run nreact ui --no-browser`, then run `pnpm dev` in `web/`. Vite serves the Vue interface at `http://127.0.0.1:5173` and proxies the API to the backend on port 8765. The development plugin obtains the local session token from the backend.

`pnpm build` writes packaged assets to `python/nreact/web_static/`. Include rebuilt assets when changing the frontend so Python-only installations receive the same interface.
