// Merge append-only event batches without applying replies for another selection.
export function mergeRunUpdate(record, update) {
  if (!record || update.id !== record.id) return record
  if (!Number.isInteger(update.offset) || update.offset < 0) throw new Error('Invalid event offset.')
  if (update.offset < record.events.length) return record
  if (update.offset !== record.events.length || !Array.isArray(update.events)
      || update.next_offset !== update.offset + update.events.length
      || !Number.isInteger(update.event_count) || update.event_count < update.next_offset
      || update.has_more !== (update.next_offset < update.event_count)
      || update.events.some((event, index) => event.index !== update.offset + index)) {
    throw new Error('Event history changed. Reopen the run to reload its recorded events.')
  }
  const events = update.events.length ? [...record.events, ...update.events] : record.events
  return { ...record, events, status: update.status, steps: update.steps,
    elapsed_seconds: update.elapsed_seconds, error: update.error, storage_error: update.storage_error,
    result: update.result || record.result, hasMore: update.has_more }
}

export const liveStatus = status => ['queued', 'running', 'pausing', 'paused', 'cancelling'].includes(status)
export const eventPreview = text => text.length > 400 ? text.slice(0, 400) + '…' : text
