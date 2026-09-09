import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { workflow, eventNode, toolRows } from '../src/graph.js'
const schema = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', 'import json; from nreact.graph import GRAPH_SCHEMA; print(json.dumps(GRAPH_SCHEMA))'], { cwd: new URL('../../', import.meta.url), encoding: 'utf8', env: { ...process.env, PYTHONPATH: fileURLToPath(new URL('../../python', import.meta.url)) } }))
const config = { model: { name: '', temperature: 0, max_tokens: 512 }, agent: { mode: 'dense', max_steps: 20, max_context_chars: 100000, max_observation_chars: 12000 }, tools: { wikipedia: true, workspace: '', custom: [] } }

test('every connection joins compatible output and input ports', () => {
  const { nodes, edges } = workflow(config, '', null, null, schema)
  for (const edge of edges) {
    const source = nodes.find(node => node.id === edge.source).data.ports.find(port => port.id === edge.sourceHandle)
    const target = nodes.find(node => node.id === edge.target).data.ports.find(port => port.id === edge.targetHandle)
    assert.equal(source.direction, 'source')
    assert.equal(target.direction, 'target')
    assert.equal(source.label, target.label)
  }
  assert.ok(edges.some(edge => edge.source === 'tools' && edge.target === 'agent'))
  assert.equal(edges.filter(edge => edge.target === 'model').length, 0)
})

test('a recorded tool action, observation and finish select the corresponding component', () => {
  assert.equal(eventNode({ kind: 'action', tool: 'search' }), 'tools')
  assert.equal(eventNode({ kind: 'observation', tool: 'search' }), 'tools')
  assert.equal(eventNode({ kind: 'thought' }), 'agent')
  assert.equal(eventNode({ kind: 'action', tool: 'finish' }), 'answer')
  assert.equal(eventNode({ kind: 'error' }), 'agent')
})

test('disabled tools never appear in the configuration graph', () => {
  const alternate = { ...config, tools: { wikipedia: false, workspace: './notes', custom: [{ name: 'lookup_record', callable: 'records:lookup', description: 'Lookup' }] } }
  assert.deepEqual(toolRows(alternate), [['read / list', './notes'], ['lookup_record', 'records:lookup']])
  assert.ok(!JSON.stringify(workflow(alternate, '', null, null, schema)).includes('Wikipedia'))
})
