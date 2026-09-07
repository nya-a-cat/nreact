# Configuration

nreact supports a local TOML file, environment variables and Python objects.

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

Use `model.api_key_env` to reference an environment variable, or save `model.api_key` directly in your local file. A saved key takes priority over the named environment variable.

`nreact.toml` is ignored by this repository's Git configuration. Keep other credential-bearing TOML files out of version control too. Saved keys are local plaintext; POSIX writes use mode 0600, and Windows uses the directory's access controls. Clearing a saved key allows the configured environment variable to supply a key again.

The Python API `save_config(config, overwrite=True)` regenerates supported TOML fields with atomic replacement. Comments and original formatting are not retained. `nreact init` never overwrites an existing file.

## Custom tools

Put a standalone `my_tools.py` beside your TOML file:

```python
def stock(product: str) -> str:
    inventory = {"notebook": 12, "pencil": 40}
    return str(inventory.get(product, 0))
```

Register the function in the TOML file:

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
