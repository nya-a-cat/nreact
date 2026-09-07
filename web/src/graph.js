export function toolRows(config) {
  return [
    ...(config.tools.wikipedia ? [['Search / Lookup', 'Wikipedia']] : []),
    ...(config.tools.workspace ? [['Read / List', config.tools.workspace]] : []),
    ...config.tools.custom.map(tool => [tool.name, tool.callable]),
  ]
}

// Ports describe the existing Python ReAct loop. Edges are derived from that
// contract; moving a node never changes how the Python agent executes.
export function workflow(config, task = '', answer = '', event = null) {
  const active = eventNode(event)
  const node = (id, title, subtitle, x, y, rows, ports) => ({ id, type: 'workbench',
    position: { x, y }, data: { title, subtitle, rows, ports, active: active === id } })
  const port = (id, label, side, direction, top) => ({ id, label, side, direction, top })
  const nodes = [
    node('task', 'Task', 'INPUT', 30, 200, [['prompt', task || 'Enter a task to begin']], [port('task', 'task', 'right', 'source', 85)]),
    node('model', 'Chat model', 'MODEL ADAPTER', 270, 25, [['model', config.model.name || 'Not configured'], ['temperature', config.model.temperature], ['max tokens', config.model.max_tokens]], [port('model', 'model', 'bottom', 'source', 50)]),
    node('agent', 'ReAct agent', 'REASONING LOOP', 270, 260, [['mode', config.agent.mode], ['max steps', config.agent.max_steps], ['context', `${config.agent.max_context_chars.toLocaleString()} chars`]], [port('task', 'task', 'left', 'target', 30), port('model', 'model', 'top', 'target', 50), port('action', 'action', 'right', 'source', 30), port('observation', 'observation', 'bottom', 'target', 50), port('finish', 'answer', 'left', 'source', 80)]),
    node('tools', 'Tool environment', 'PYTHON DISPATCH', 560, 220, toolRows(config).length ? toolRows(config) : [['registry', 'No external tools']], [port('action', 'action', 'left', 'target', 30), port('result', 'result', 'bottom', 'source', 50)]),
    node('observation', 'Observation', 'TOOL RESULT', 560, 455, [['limit', `${config.agent.max_observation_chars.toLocaleString()} chars`], ['return', 'Append to context']], [port('result', 'result', 'top', 'target', 50), port('observation', 'observation', 'left', 'source', 60)]),
    node('answer', 'Answer', 'OUTPUT', 30, 455, [['answer', answer || 'Waiting for Finish[…]']], [port('answer', 'answer', 'top', 'target', 50)]),
  ]
  const edge = (source, target, sourceHandle, targetHandle, layer, label) => ({
    id: `${source}-${target}`, source, target, sourceHandle, targetHandle, type: 'smoothstep', label,
    class: `wire-${layer}`, data: { layer }, markerEnd: 'arrowclosed',
    style: { stroke: layer === 'action' ? '#c24991' : layer === 'config' ? '#a6a292' : '#f2e1ac', strokeWidth: 1.7, ...(layer === 'config' ? { strokeDasharray: '5 5' } : {}) },
    labelStyle: { fill: '#dedbcf', fontSize: 10 }, labelBgStyle: { fill: '#222426' }, labelBgPadding: [5, 3],
  })
  return { nodes, edges: [edge('task', 'agent', 'task', 'task', 'config', 'task'), edge('model', 'agent', 'model', 'model', 'config', 'model'), edge('agent', 'tools', 'action', 'action', 'action', 'action'), edge('tools', 'observation', 'result', 'result', 'observation', 'result'), edge('observation', 'agent', 'observation', 'observation', 'observation', 'observation'), edge('agent', 'answer', 'finish', 'answer', 'action', 'finish')] }
}

export function eventNode(event) {
  if (!event) return null
  if (event.kind === 'observation') return 'observation'
  if (event.kind === 'finish' || (event.kind === 'action' && event.tool?.toLowerCase() === 'finish')) return 'answer'
  if (event.kind === 'action' && !['finish', 'think'].includes(event.tool?.toLowerCase())) return 'tools'
  return 'agent'
}
