# nreact

A small, pure Python [ReAct](https://arxiv.org/abs/2210.03629) agent. Use it from the terminal or import it into your application.

nreact implements interleaved reasoning, tool execution and environment feedback. It includes the authors' HotpotQA and FEVER demonstrations, a Wikipedia environment, local file-reading tools, and JSONL episode traces. Python 3.10+; zero runtime dependencies.

## Installation

```sh
git clone https://github.com/nya-a-cat/nreact.git
cd nreact
uv sync --locked
uv run nreact demo
```

The demo uses a scripted model and fictional pages. It runs offline and demonstrates the execution protocol. See [the reproduction notes](docs/reproduction.md) for the relationship to the paper and the validation scope.

To install the command into your user tool environment from a checkout:

```sh
uv tool install .
nreact --help
```

## Usage

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

## Python API

```python
from nreact import Agent, ChatModel, Tool, ToolEnvironment

inventory = {"notebook": 12, "pencil": 40}
tools = ToolEnvironment([
    Tool("stock", "Return units available for a product name.",
         lambda product: str(inventory.get(product, 0))),
])

agent = Agent(ChatModel.from_env(), tools, mode="dense", max_steps=10)
result = agent.run("How many notebooks are available?")
print(result.status, result.answer)
```

Tool functions accept a string and return a string. JSON can be used inside an argument for structured inputs. Names are case-insensitive; `think` and `finish` are reserved. Tool exceptions become error observations so the model can decide what to do next.

For an entirely offline API example, run `uv run python examples/custom_tool.py`.

Use the paper's question-answering demonstrations:

```python
from nreact import Agent, ChatModel
from nreact.paper import paper_examples
from nreact.wiki import WikiEnvironment

agent = Agent(
    ChatModel.from_env(),
    WikiEnvironment(),
    examples=paper_examples("hotpotqa"),
    max_steps=7,
)
result = agent.run("Who wrote Pride and Prejudice?")
```

Applications can implement `Model.generate(prompt, *, stop)` and `Environment.reset()/step(name, argument)` to integrate their own models and environments. See [the API guide](docs/api.md). Each agent/environment pair should be used sequentially; create separate instances for concurrent episodes.

## How it works

1. Build context from the task, tool descriptions, few-shot examples and previous turns.
2. Generate one turn containing a thought, an action, or both, according to the selected mode.
3. Parse the action and execute the corresponding tool.
4. Append the actual environment observation and repeat.
5. End on `Finish[answer]`, an environment terminal state, or a configured limit/error.

In `dense` mode, each turn contains `Thought N:` and `Action N:`. In `sparse` mode, thoughts can occur on their own and environment actions can follow each other. Thought-only turns do not call the environment. Each generation consumes one step, including malformed output.

The implementation resides in a few modules: `_core.py` handles the protocol and state, `agent.py` orchestrates execution, `models.py` calls the model, and `tools.py` / `wiki.py` provide environments.

## Evaluation

Provide a JSONL file with an explicit task and accepted answers per row:

```json
{"id":"q1","task":"Who wrote Pride and Prejudice?","answers":["Jane Austen"]}
```

```sh
uv run nreact eval examples/qa.jsonl --paper hotpotqa --limit 2 --output runs/qa-001
```

The evaluator saves the dataset hash, selected IDs, seed, per-episode traces, statuses and normalized answer exact match. Failed episodes remain in the denominator. This is a QA evaluator; FEVER uses label answers. It does not compute evidence-retrieval scores, supporting-fact scores or the official FEVER score. The bundled two-question file is an example input.

The current release implements the ReAct execution mechanism and reusable tooling. Published benchmark scores, full ALFWorld/WebShop integrations, CoT/self-consistency hybrids and finetuning have not been reproduced. See [reproduction details](docs/reproduction.md) before comparing results.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
```

Tests exercise the protocol, state transitions, environments, trace/evaluation output, CLI and the HTTP adapter against a local test server. CI runs them on Windows, Linux and macOS. Tests require no API key or external network access.

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

MIT. Bundled upstream demonstrations retain the authors' copyright and license; see [NOTICE](NOTICE).
