# Local workbench

Run `uv run nreact ui` from the directory containing `nreact.toml`. Use `--config path/to/config.toml`, `--port 8765` and `--no-browser` to control startup. A new configuration can be created from the interface when the file is missing.

## Configuration

The component library selects Task, Chat model, ReAct agent, Tool environment, Observation and Answer. Adding Wikipedia, a workspace or a Python function updates the tool registry. Configure function names, descriptions and `module:function` handlers in the inspector. Handlers load when a run starts.

Save property changes with the toolbar or Ctrl/Cmd+S. The TOML tab edits the same configuration. Save one editor before switching to the other. Invalid values leave the previous file intact; a file modified externally requires a reload before saving.

API keys can use an environment variable or a value stored in the local TOML file. The preview and exported TOML omit saved keys. Saving through the TOML editor preserves the existing key. Use the model inspector to replace or clear it. Clearing a saved key allows the configured environment variable to supply credentials.

## Canvas

The graph follows the existing ReAct execution loop: a task and model feed the agent, actions invoke tools, observations return to context, and Finish produces an answer. Connections are derived from this contract. Drag nodes to arrange the view, drag empty space to pan, and scroll to zoom. F fits the graph; View → Reset node positions restores the initial arrangement. Tab to a node and press Enter to open its properties, or use arrow keys to move it (Shift moves farther). Positions are stored in the browser for each configuration path.

Layers controls the visibility of configuration, action and observation connections. At widths below 1250 pixels the component library becomes a drawer. Below 1000 pixels the inspector also becomes a drawer. Left-rail icons open each panel; the close button or shaded backdrop closes it.

## Execution and replay

Run uses the saved configuration and task. Only one run can be active per server. The offline demo uses a scripted model and fictional Wikipedia pages, independent of the working configuration.

| Control | Behavior |
| --- | --- |
| Run / Resume | Continue until completion or a configured limit. |
| Step | Execute one model turn, including its tool call when applicable, then wait. |
| Pause | Finish the current turn and wait before the next model call. |
| Stop | Request cooperative cancellation. In-flight requests and tools return before execution can end. |
| Previous / next event | Select an existing event; no model or tool call occurs. |
| Latest | Select the newest event and follow new events during execution. |

Selecting an event synchronizes the graph, outliner and full content in the Event tab. Previous/next on the timeline keeps the drawer closed when browsing in a narrow window; the Event inspector has its own navigation. The list follows the selected event. The Result tab shows the answer, termination details and usage returned by the model. Elapsed time includes time spent paused. The UI does not infer unavailable usage or prices.

Run history opens the saved configuration snapshot and recorded events. Select Working copy to return to the current editable configuration. Return to active run restores its execution controls. File → Copy run task to working copy reuses a recorded prompt with the current configuration. The most recent 50 runs appear in the history list.

File → Export saved TOML and Export selected run write unique files under `.nreact/exports/`. The confirmation shows the local file path and contents, with copy buttons. Saved configuration exports omit the API key. This flow also works in embedded browsers that do not support downloads. Reloading a modified configuration presents an in-page choice to keep editing or discard the draft.

## Local storage

Each run writes a JSONL execution trace and a completed JSON record under `.nreact/runs/`, relative to the configuration directory. Completed records remain readable after restart. An interrupted server can leave an incomplete JSONL trace; it does not resume that execution automatically. Task, model output and tool content are stored as supplied; configuration snapshots omit the API key field.

The HTTP server binds to `127.0.0.1`. API requests require the session token provided by the local page and matching host/origin headers. JavaScript, styles and fonts are bundled with the Python package. Model calls, Wikipedia requests and custom-tool behavior follow the configuration.
