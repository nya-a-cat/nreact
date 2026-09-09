// Keep this data-only validator aligned with nreact.workflows.validate_graph.
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const keysAre = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const coordinate = value => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1_000_000
const edgeKeys = ['source', 'sourceHandle', 'target', 'targetHandle']
export const edgeIdentity = edge => edgeKeys.map(key => edge[key]).join('\u0000')
export const clone = value => JSON.parse(JSON.stringify(value))

export function validateGraph(value, schema) {
  if (!object(value) || !schema || value.version !== schema.version || !Number.isInteger(value.version) ||
      Object.keys(value).some(key => !['version', 'positions', 'connections', 'viewport'].includes(key))) {
    throw new Error('Unsupported graph document.')
  }
  const known = new Set(schema.nodes.map(node => node.id))
  if (!object(value.positions) || Object.keys(value.positions).some(id => !known.has(id))) throw new Error('Graph positions must reference known nodes.')
  for (const position of Object.values(value.positions)) {
    if (!keysAre(position, ['x', 'y']) || !coordinate(position.x) || !coordinate(position.y)) throw new Error('Graph coordinates must be finite numbers between -1000000 and 1000000.')
  }
  if (!Array.isArray(value.connections) || value.connections.length > 32) throw new Error('Invalid graph connections.')
  const allowed = new Set(schema.connections.map(edgeIdentity)), seen = new Set()
  for (const edge of value.connections) {
    if (!keysAre(edge, edgeKeys) || edgeKeys.some(key => typeof edge[key] !== 'string') || !allowed.has(edgeIdentity(edge))) throw new Error('Connection ports are incompatible with the ReAct components.')
    if (seen.has(edgeIdentity(edge))) throw new Error('Duplicate graph connection.')
    seen.add(edgeIdentity(edge))
  }
  if (Object.hasOwn(value, 'viewport')) {
    const view = value.viewport
    if (!keysAre(view, ['x', 'y', 'zoom']) || !coordinate(view.x) || !coordinate(view.y) ||
        typeof view.zoom !== 'number' || !(view.zoom >= .3 && view.zoom <= 1.6)) throw new Error('Invalid graph viewport.')
  }
  return clone(value)
}
