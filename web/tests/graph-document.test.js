import test from 'node:test'
import assert from 'node:assert/strict'
import { validateGraph } from '../src/graph-document.js'

const edge = { source: 'model', sourceHandle: 'model', target: 'agent', targetHandle: 'model' }
const schema = { version: 2, nodes: [{ id: 'model' }, { id: 'agent' }], connections: [edge] }
const graph = () => ({ version: 2, positions: { model: { x: 0, y: 0 } }, connections: [edge] })

test('graph validation returns isolated data and permits incomplete connections', () => {
  const value = graph(), result = validateGraph(value, schema)
  result.positions.model.x = 9
  assert.equal(value.positions.model.x, 0)
  assert.deepEqual(validateGraph({ ...value, connections: [] }, schema).connections, [])
})
test('graph rejects unknown nodes, coordinates, versions and fields', () => {
  for (const value of [null, [], {}, { ...graph(), version: 3 }, { ...graph(), script: 'x' },
    { ...graph(), positions: { unknown: { x: 1, y: 2 } } },
    ...[NaN, Infinity, '1', null, true, 1_000_001].map(x => ({ ...graph(), positions: { model: { x, y: 1 } } }))]) {
    assert.throws(() => validateGraph(value, schema))
  }
})
test('graph rejects duplicate and incompatible or malformed connections', () => {
  for (const connections of [[edge, edge], [null], [{ ...edge, source: 'agent' }], [{ ...edge, hidden: true }]]) {
    assert.throws(() => validateGraph({ ...graph(), connections }, schema))
  }
})
test('graph rejects prototype-bearing JSON node names', () => {
  assert.throws(() => validateGraph(JSON.parse('{"version":2,"positions":{"__proto__":{"x":1,"y":2}},"connections":[]}'), schema))
  assert.equal({}.x, undefined)
})
test('bounded viewport validation', () => {
  assert.deepEqual(validateGraph({ ...graph(), viewport: { x: 1, y: 2, zoom: .5 } }, schema).viewport, { x: 1, y: 2, zoom: .5 })
  for (const zoom of [0, 3, null, '1', NaN, Infinity]) assert.throws(() => validateGraph({ ...graph(), viewport: { x: 0, y: 0, zoom } }, schema))
})
