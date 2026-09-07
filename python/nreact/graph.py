"""Workbench bindings for build_agent(config) and Agent.run(task).

The graph describes construction dependencies. Agent.run owns the ReAct loop;
observations and actions are recorded events within that loop.
"""


def field(path, kind="text", **options):
    return {"path": path, "label": path.split(".")[-1], "kind": kind, **options}


GRAPH_SCHEMA = {
    "version": 2,
    "nodes": [
        {"id": "model", "title": "ChatModel", "subtitle": "nreact.models.ChatModel", "position": {"x": 0, "y": 0},
         "fields": [field("model.name"), field("model.base_url"),
                    field("model.temperature", "number", min=0, max=2, step=0.1),
                    field("model.max_tokens", "number", min=1, max=131072),
                    field("model.timeout", "number", min=1, max=300),
                    field("model.send_stop", "boolean"), field("model.api_key_env")],
         "inputs": [], "outputs": [{"id": "model", "type": "Model"}]},
        {"id": "tools", "title": "ConfiguredEnvironment", "subtitle": "nreact.config.ConfiguredEnvironment", "position": {"x": 0, "y": 460},
         "fields": [field("tools.wikipedia", "boolean"), field("tools.workspace"), field("tools.custom", "tools")],
         "inputs": [], "outputs": [{"id": "environment", "type": "Environment"}]},
        {"id": "task", "title": "Task", "subtitle": "Agent.run(task)", "position": {"x": 350, "y": 0},
         "fields": [field("task", "textarea", maxLength=16000)],
         "inputs": [], "outputs": [{"id": "task", "type": "str"}]},
        {"id": "agent", "title": "Agent", "subtitle": "nreact.agent.Agent", "position": {"x": 350, "y": 260},
         "fields": [field("agent.mode", "select", options=["dense", "sparse"]),
                    field("agent.max_steps", "number", min=1, max=1000),
                    field("agent.max_context_chars", "number", min=1, max=2000000),
                    field("agent.max_observation_chars", "number", min=1, max=250000),
                    field("agent.paper", "select", options=["", "hotpotqa", "fever"])],
         "inputs": [{"id": "model", "type": "Model"}, {"id": "environment", "type": "Environment"}, {"id": "task", "type": "str"}],
         "outputs": [{"id": "result", "type": "Result"}]},
        {"id": "answer", "title": "Result", "subtitle": "nreact.types.Result", "position": {"x": 710, "y": 260},
         "fields": [], "inputs": [{"id": "result", "type": "Result"}], "outputs": []},
    ],
    "connections": [
        {"source": source, "sourceHandle": output, "target": target, "targetHandle": input_}
        for source, output, target, input_ in [
            ("model", "model", "agent", "model"),
            ("tools", "environment", "agent", "environment"),
            ("task", "task", "agent", "task"),
            ("agent", "result", "answer", "result"),
        ]
    ],
}

REACT_CONNECTIONS = {
    tuple(edge[key] for key in ("source", "sourceHandle", "target", "targetHandle"))
    for edge in GRAPH_SCHEMA["connections"]
}
