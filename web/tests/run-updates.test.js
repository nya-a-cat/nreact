import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mergeRunUpdate, liveStatus, eventPreview } from '../src/run-updates.js'

const initial = () => ({ id: 'run', events: [], result: null, config: { model: 'fixture' }, task: 'Task', status: 'running' })
const delta = (overrides = {}) => ({ id: 'run', offset: 0, next_offset: 1, event_count: 1, events: [{ index: 0, text: 'hello' }], has_more: false, result: null, status: 'running', steps: 1, ...overrides })

test('append-only merge preserves configuration and full event content', () => {
  const before = initial(), text = 'x'.repeat(10000)
  const next = mergeRunUpdate(before, delta({ events: [{ index: 0, text }] }))
  assert.equal(next.config, before.config)
  assert.equal(next.task, before.task)
  assert.equal(next.events[0].text, text)
  assert.equal(before.events.length, 0)
})

test('duplicate or late replies cannot overwrite the newer record', () => {
  const record = mergeRunUpdate(initial(), delta())
  assert.equal(mergeRunUpdate(record, delta({ status: 'paused' })), record)
  assert.equal(mergeRunUpdate(record, delta({ id: 'other' })), record)
})

test('empty updates reuse the event array and publish the final result', () => {
  const record = mergeRunUpdate(initial(), delta())
  const next = mergeRunUpdate(record, delta({ offset: 1, next_offset: 1, events: [], status: 'finished', result: { answer: 'ok' } }))
  assert.equal(next.events, record.events)
  assert.equal(next.result.answer, 'ok')
  assert.equal(next.status, 'finished')
})

test('batched catch-up retains the cursor and only accepts contiguous indices', () => {
  const first = mergeRunUpdate(initial(), delta({ event_count: 2, has_more: true }))
  assert.equal(first.hasMore, true)
  const final = mergeRunUpdate(first, delta({ offset: 1, next_offset: 2, event_count: 2, events: [{ index: 1, text: 'second' }] }))
  assert.equal(final.events.length, 2)
  assert.equal(final.hasMore, false)
})

test('invalid gaps and malformed cursor metadata request a clean reload', () => {
  for (const changes of [{ offset: -1 }, { offset: 1 }, { next_offset: 8 }, { event_count: 0 }, { has_more: true }, { events: {} }, { events: [{ index: 5 }] }]) {
    assert.throws(() => mergeRunUpdate(initial(), delta(changes)), /offset|history/)
  }
})

test('previews are bounded while lifecycle polling covers queued and paused runs', () => {
  assert.equal(eventPreview('x'.repeat(10000)).length, 401)
  assert.equal(eventPreview('hello'), 'hello')
  for (const state of ['queued', 'running', 'paused', 'pausing', 'cancelling']) assert.equal(liveStatus(state), true)
  for (const state of ['finished', 'cancelled', 'interrupted', 'error', undefined]) assert.equal(liveStatus(state), false)
})
