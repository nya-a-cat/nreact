import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ApiError, createApi } from '../src/api.js'

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })

test('transport sends read and single mutating requests with the local token', async () => {
  const seen = []
  const api = createApi('local-token', { fetchImpl: async (...args) => { seen.push(args); return response({ ok: true }) } })
  assert.deepEqual(await api('runs/state'), { ok: true })
  assert.deepEqual(await api('run', { task: 'Task' }), { ok: true })
  assert.equal(seen.length, 2)
  assert.equal(seen[0][0], '/api/runs/state')
  assert.equal(seen[0][1].method, undefined)
  assert.equal(seen[0][1].headers['X-Nreact-Token'], 'local-token')
  assert.equal(seen[1][1].method, 'POST')
  assert.equal(seen[1][1].body, JSON.stringify({ task: 'Task' }))
})

test('HTTP conflicts retain status and safe server explanation', async () => {
  const api = createApi('', { fetchImpl: async () => response({ error: 'Reload the current version.' }, 409) })
  await assert.rejects(api('workflow', {}), error => error instanceof ApiError && error.status === 409 && error.message.includes('Reload'))
})

test('network failures are sanitized and never repeat a possibly accepted write', async () => {
  let calls = 0
  const api = createApi('', { fetchImpl: async () => { calls++; throw new Error('fixture-secret/private-url') } })
  await assert.rejects(api('run', {}), error => error.status === 0 && error.message.includes('may have been applied') && !error.message.includes('fixture-secret'))
  assert.equal(calls, 1)
})

test('abort timeout is bounded and distinguishes read recovery from write uncertainty', async () => {
  let calls = 0
  const fetchImpl = (url, { signal }) => new Promise((resolve, reject) => {
    calls++; signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
  })
  const api = createApi('', { fetchImpl, timeoutMs: 5 })
  await assert.rejects(api('runs/state'), /timed out.*read-only/)
  await assert.rejects(api('run', {}), /timed out.*may have been applied/)
  assert.equal(calls, 2)
})

test('invalid JSON after a write explains that its outcome is uncertain', async () => {
  const api = createApi('', { fetchImpl: async () => ({ ok: true, status: 202, json: async () => { throw new Error('bad JSON') } }) })
  await assert.rejects(api('run', {}), /invalid JSON.*may have been applied/)
  await assert.rejects(api('runs/state'), /invalid JSON/)
})

test('non-string error fields use the HTTP status instead of serializing arbitrary objects', async () => {
  const api = createApi('', { fetchImpl: async () => response({ error: { private: 'secret' } }, 500) })
  await assert.rejects(api('runs'), /Request failed \(500\)/)
})
