# Workflows

A workflow groups model and tool settings, a task, node positions, viewport and
connections in one versioned JSON document. Use the **File** menu in the local
workbench to save, open, import or export workflows.

## Save and open

**Save workflow…** writes a named snapshot under `.nreact/workflows/`, beside
`nreact.toml`. Saving an opened workflow under the same name updates its record.
Changing its name creates a new copy. Model API key values are omitted. A pending
API key draft stays in the editor and is excluded from the workflow.

The workflow snapshot captures property drafts and the task. Save or reload an
open TOML draft first. **Save configuration** continues to write the executable
TOML separately; running requires a saved configuration and a complete graph.

**Open workflow…** lists up to 200 recent saved workflows. Choose a workflow to
review its configuration and task. **Replace working copy** replaces current
property, TOML and task drafts and loads its graph. It does not execute tools or
write the working TOML. Review the endpoint and tool handlers, configure credentials,
then save the configuration before running. The current working theme stays in
place until the selected workflow's configuration is applied.

Each saved record includes a revision. Updates and deletions from another browser
tab cause a conflict instead of overwriting newer data. Reopen the workflow to
review the latest version, or save the current draft under a new name. Deletion
requires confirmation and removes only the selected workflow file.

## Import and export

**Export workflow** writes a portable JSON file under `.nreact/exports/` and opens
the existing export dialog with its path and contents. Use **Import workflow…**
to select a JSON file. The importer validates its structure, fields, node IDs,
coordinates and compatible ports before showing the preview. Invalid files leave
the current working copy unchanged. Cancelling the preview preserves all drafts.

A portable document has this structure:

```json
{
  "format": "nreact.workflow",
  "version": 1,
  "name": "Project question",
  "config": {
    "model": { "name": "local-model", "base_url": "http://127.0.0.1:8080/v1" },
    "tools": { "wikipedia": false, "workspace": "." }
  },
  "task": "Read README.md and summarize this project.",
  "graph": { "version": 2, "positions": {}, "connections": [] }
}
```

This example is an incomplete graph: restore or connect the four default links
before executing it. The document format supports the workbench's five fixed
ReAct component roles. ComfyUI documents and arbitrary node plugins use different
schemas and are rejected. Relative workspace paths and custom Python modules
resolve beside the active configuration, so review them after moving a workflow.

Imported files clear the credential environment-variable name, OAuth mode and
OAuth file reference. Opening any workflow also schedules the saved TOML API key
for clearing when the configuration is next saved. Set credentials explicitly in
Model properties or TOML. Files containing a `model.api_key` field are rejected.
Local saved workflows can retain non-secret environment-variable names and OAuth
file references. Tasks, descriptions and paths remain in exported files; inspect
them before sharing.

## Canvas editing

Undo and redo cover node moves, connection changes, graph loading and layout
resets. Up to fifty changes are retained in the current browser session. Loading
a workflow leaves the executable configuration separate from graph undo: undoing
a graph edit changes positions and links only. Viewport changes do not create undo
entries. Mouse panning and zooming retain the current view; **F** fits all nodes.

Run snapshots use a temporary view. Moving nodes while inspecting a run leaves
the working graph and its saved layout intact. Returning to the working copy
restores its viewport. Invalid browser-saved graph data falls back to a usable
default layout with an explanatory message.

## Storage

Workflow JSON is limited to 60,000 UTF-8 bytes, with tasks up to 16,000 characters
and names up to 120 characters. Files are atomically replaced after syncing their
contents. New files request owner-only access on POSIX; Windows uses directory
ACLs. Use one server per configuration directory. Filesystem and power-loss
behavior still apply; the containing directory is not fsynced after replacement.

The authenticated local API exposes `GET /api/workflows`, `GET /api/workflow?id=…`,
`POST /api/workflow`, `POST /api/workflow/validate` and `POST /api/workflow/delete`.
Validation and opening are read-only. Saving and deletion require the record's
current revision when operating on an existing ID.
