# ReAct implementation and reproduction scope

## Sources

- [ReAct paper, ICLR 2023](https://arxiv.org/pdf/2210.03629), especially Sections 2–4 and Appendix C.
- [Author implementation](https://github.com/ysymyth/ReAct/tree/6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9), pinned at `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`.
- Reference files: `hotpotqa.ipynb`, `FEVER.ipynb`, `alfworld.ipynb`, `wikienv.py`, and the two QA prompt JSON files.

## Implemented mechanism

The paper augments environment actions with language thoughts. Thoughts update the agent's context; environment actions obtain new observations. nreact retains this distinction in `Session.advance()` and `Session.observe()`. The model remains frozen during an episode.

`dense` requires a thought followed by an action per turn, corresponding to the paper's knowledge-intensive reasoning setup. `sparse` supports thought-only turns, action-only turns and combined turns, corresponding to the flexible placement of thoughts in interactive tasks. Agent history retains every accepted turn and actual observation.

The bundled HotpotQA prompt contains six demonstrations (`webthink_simple6`); FEVER contains three (`webthink_simple3`). Their text is copied unchanged from the pinned upstream files, with the original license. Tests check the number of demonstrations and replay their protocol against the parser.

Wikipedia actions preserve the intent of `Search`, `Lookup` and `Finish`: exact title lookup, first five sentences, successive matching sentences and explicit termination. Missing-title suggestions and repeated keyword lookup are covered by tests.

## Engineering differences

- The default adapter uses a configurable Chat Completions server. The paper primarily used PaLM-540B, and the public notebooks used `text-davinci-002`. A selected modern model is a separate experimental condition.
- nreact requests full labeled turns. The reference HotpotQA notebook prefills `Thought N:` and uses an extra action-generation call after certain malformed responses. Here an invalid response consumes a step and adds a protocol-error observation.
- Sparse actions use a shared `Tool[argument]` syntax. ALFWorld's original `> think:` and free-text action serialization are not implemented as an ALFWorld adapter.
- Wikipedia uses MediaWiki text extracts and returns source URLs. The reference scrapes HTML paragraphs/lists. Page content, extraction and search ranking can differ. A failed search clears the current page to avoid accidental lookup in an earlier result.
- The controller returns a distinct `max_steps` status when exhausted. The reference QA notebook calls `finish[]` after seven unsuccessful turns. For QA comparisons, nreact's evaluator scores exhausted runs as zero.
- Limits, HTTP timeouts, optional observation truncation and strict output parsing are explicit application settings. Retain them with any recorded results.

## Validation and remaining experiments

The test suite validates execution semantics with deterministic model fixtures and a local HTTP server. The offline demonstration uses fictional pages. These checks establish that the implementation runs and that observations affect subsequent prompts.

The release does **not** claim reproduction of the paper's benchmark scores. No full HotpotQA/FEVER evaluation, ALFWorld/WebShop evaluation, baseline comparison, ReAct–CoT/self-consistency hybrid, human trajectory editing study or finetuning experiment has been completed. The generic environment interface can host future task adapters.

To perform a model-specific QA evaluation, supply a fixed dataset, model endpoint and limits to `nreact eval`. Save the selected dataset IDs, dataset hash, model version, inference parameters, all trajectories and results. Live Wikipedia evaluations additionally depend on the retrieval date and returned page content. Compare methods only under matched model, prompt, data and retrieval settings. A small or simplified test cannot establish the full paper method's effectiveness.
