# nreact

[![Tests](https://github.com/nya-a-cat/nreact/actions/workflows/ci.yml/badge.svg)](https://github.com/nya-a-cat/nreact/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A pure Python library for building agents with [ReAct](https://arxiv.org/abs/2210.03629).

Give an agent a model and tools. It reasons about the task, calls a tool, reads the result, and continues until it produces an answer. Use the Python API in your application or run an agent from the terminal.

- **ReAct reasoning:** thoughts and actions can alternate on every turn or occur as needed.
- **Custom tools:** connect Python functions, local files, or your own interactive environment.
- **Model adapters:** use an OpenAI-compatible endpoint, ChatGPT OAuth, or the small `Model` interface.
- **Run records:** save thoughts, actions, observations, token usage and termination status as JSONL.
- **Local workbench:** edit TOML, inspect the agent graph, execute one turn at a time and browse saved runs in a Vue interface.

Python 3.10+ · Standard-library runtime on Python 3.11+ · Windows, Linux and macOS

## Installation

```sh
git clone https://github.com/nya-a-cat/nreact.git
cd nreact
uv sync --locked
```

Try `uv run nreact demo` for an offline, scripted example that requires no model server or API key.

## Quickstart

Create a local configuration file:

```sh
uv run nreact init
```

Edit the model and tool settings in `nreact.toml`. The CLI automatically reads that file; use `--config path/to/config.toml` to choose another. See [TOML configuration](docs/configuration.md) for the file format and custom tools.

### ChatGPT authentication

```sh
uv run nreact auth login
uv run nreact auth status
```

Set `model.auth = "chatgpt"` and choose a Codex model available to your account. Use `auth login --device` for device-code authorization and `auth logout` to clear nreact's credentials. The backend uses its own credential cache and refreshes tokens during model calls. See [OpenAI authentication](docs/authentication.md) for storage, configuration and generation settings.

### Local workbench

```sh
uv run nreact ui
```

The workbench opens at `http://127.0.0.1:8765`. Edit **ChatModel** and **ConfiguredEnvironment** directly on the canvas, save, then enter a **Task**. **Try offline demo** walks through a scripted example with fictional pages and no external requests.

**Step** executes one complete ReAct turn. **Pause** waits for the next turn boundary; **Stop** prevents the next operation after an in-flight call returns. Select an event to highlight its component and inspect the recorded content. The previous/next buttons browse history without executing tools.

The graph binds backend-defined ChatModel, ConfiguredEnvironment, Task, Agent and Result components. Parameters edit the same TOML configuration used by the CLI. Drag nodes, connect compatible ports, or select a wire and press Delete. Incomplete connections block execution; undo and redo restore connection edits. The side panels collapse into drawers in narrow windows. Open them from the left icon rail, and use **View → Reset node positions** to restore the layout.

Completed runs persist beside the configuration in `.nreact/runs/`. Configuration snapshots omit API keys; task and tool content remains in the trace. See the [workbench guide](docs/workbench.md) for controls and storage details.

Use **File → Save workflow** to keep model and tool settings, the task and canvas together. Open saved workflows or import a workflow JSON file, review the configuration, then save before running. Node moves, layout resets and connections support undo/redo; run snapshots keep the working layout intact. See [Workflows](docs/workflows.md) for storage, portability and credential handling.

### Python API

Set `NREACT_MODEL` and `NREACT_BASE_URL` for your model endpoint, plus `NREACT_API_KEY` if required. See [model configuration](docs/usage.md) for examples.

Create an agent with access to the current directory:

```python
from nreact import Agent, ChatModel, ToolEnvironment, workspace_tools

model = ChatModel.from_env()
tools = ToolEnvironment(workspace_tools("."))
agent = Agent(model, tools)

result = agent.run("Read README.md and explain how to use this project.")
print(result.answer)
```

The model can list directories and read files under the selected path. File contents are sent to the configured model endpoint. Save the example as `main.py` and run it with `uv run python main.py`.

The same task from the terminal:

```sh
uv run nreact run "Read README.md and explain how to use this project." --workspace .
```

## Custom tools

Wrap a Python function in `Tool` to make it available to the agent:

```python
from nreact import Agent, ChatModel, Tool, ToolEnvironment

def lookup(name: str) -> str:
    records = {"language": "Python", "license": "MIT"}
    return records.get(name, "No matching record.")

tools = ToolEnvironment([Tool("lookup", "Look up a project property by name.", lookup)])
agent = Agent(ChatModel.from_env(), tools)
result = agent.run("What language and license does the project use?")
print(result.answer)
```

Tools accept and return strings; structured arguments can use JSON. For stateful tasks, implement an [environment](docs/api.md#custom-environments) with `reset()` and `step()`.

## ReAct

The implementation follows the reasoning–action–observation loop introduced by [Yao et al. (ICLR 2023)](https://arxiv.org/abs/2210.03629). `mode="dense"` generates a thought before each action; `mode="sparse"` lets the model decide when to think.

The authors' HotpotQA and FEVER few-shot examples are bundled with a Wikipedia `Search`/`Lookup` environment:

```sh
uv run nreact run "Who wrote Pride and Prejudice?" --paper hotpotqa
```

This release implements the agent loop. The paper's benchmark scores and finetuning results have not been reproduced. [Reproduction notes](docs/reproduction.md) document the implemented components and differences from the reference code.

## Documentation

- [Configuration](docs/configuration.md): TOML and custom tool registration.
- [OpenAI authentication](docs/authentication.md): ChatGPT OAuth and credential management.
- [Usage guide](docs/usage.md): model setup, command-line options and traces.
- [API reference](docs/api.md): agents, tools, models, environments and results.
- [Reproduction notes](docs/reproduction.md): paper prompts, evaluation and implementation details.
- [Offline example](examples/custom_tool.py): a complete custom-tool agent with a scripted model.

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build
```

Tests run without API keys or external network access. CI covers Windows, Linux and macOS.

The installed Python package includes the compiled interface and local fonts. To edit the Vue frontend:

```sh
cd web
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
pnpm test
```

For development, keep `uv run nreact ui --no-browser` running from the repository root and run `pnpm dev` in `web/`. Vite proxies the local API. Rebuild the assets before packaging with `uv build`.

Python 3.10 uses the `tomli` compatibility package.

Browser regressions run against the packaged interface with Chromium and the real local HTTP server. To run them locally:

```sh
uv run --with playwright==1.57.0 python -m playwright install chromium
uv run --with playwright==1.57.0 python -m unittest discover -s tests/browser -v
```

The browser CI job retains screenshots, console output and Playwright traces for 14 days. Its tests use isolated temporary configurations and scripted models.

## Citation

```bibtex
@inproceedings{yao2023react,
  title={ReAct: Synergizing Reasoning and Acting in Language Models},
  author={Yao, Shunyu and Zhao, Jeffrey and Yu, Dian and Du, Nan and
          Shafran, Izhak and Narasimhan, Karthik and Cao, Yuan},
  booktitle={International Conference on Learning Representations},
  year={2023},
  url={https://arxiv.org/abs/2210.03629}
}
```

## License

[MIT](LICENSE). Bundled ReAct demonstrations retain the authors' copyright and license; see [NOTICE](NOTICE).
