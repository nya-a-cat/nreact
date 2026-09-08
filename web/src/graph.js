export function toolRows(config) {
  return [...(config.tools.wikipedia ? [['search / lookup', 'Wikipedia']] : []), ...(config.tools.workspace ? [['read / list', config.tools.workspace]] : []), ...config.tools.custom.map(tool => [tool.name, tool.callable])]
}
export function workflow(config, task = '', result = null, event = null, schema = null) {
  if (!schema) return { nodes: [], edges: [] }
  const active = eventNode(event)
  const nodes = schema.nodes.map(definition => {
    const ports = [...definition.inputs.map((port, index) => ({ ...port, label: port.type, side: 'left', direction: 'target', offset: 72 + index * 25 })), ...definition.outputs.map((port, index) => ({ ...port, label: port.type, side: 'right', direction: 'source', offset: 72 + index * 25 }))]
    const fields = definition.fields.map(field => ({ ...field, value: field.path === 'task' ? task : field.path.split('.').reduce((value, key) => value?.[key], config) }))
    return { id: definition.id, type: 'workbench', position: { ...definition.position }, data: { ...definition, ports, fields, active: active === definition.id, portHeight: Math.max(definition.inputs.length, definition.outputs.length) * 25, tools: definition.id === 'tools' ? toolRows(config) : [], result: definition.id === 'answer' ? result : null } }
  })
  const edges = schema.connections.map(connection => ({ ...connection, id: `${connection.source}:${connection.sourceHandle}->${connection.target}:${connection.targetHandle}`, type: 'outlined', data: { layer: connection.sourceHandle === 'result' ? 'action' : 'config' }, style: { stroke: connection.sourceHandle === 'result' ? 'var(--wire-action)' : 'var(--wire-config)', strokeWidth: 4 } }))
  return { nodes, edges }
}
export function eventNode(event) {
  if (!event) return null
  if (event.kind === 'observation') return 'tools'
  if (event.kind === 'finish' || (event.kind === 'action' && event.tool?.toLowerCase() === 'finish')) return 'answer'
  if (event.kind === 'action' && !['finish', 'think'].includes(event.tool?.toLowerCase())) return 'tools'
  return 'agent'
}
