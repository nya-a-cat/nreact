// Local-session transport. Mutating requests are never retried automatically.
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status }
}

export function createApi(token, { fetchImpl = globalThis.fetch, timeoutMs = 15000 } = {}) {
  return async function api(path, payload) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const writing = payload !== undefined
    const uncertain = ' This operation may have been applied. Check history or reload saved data before retrying.'
    try {
      const response = await fetchImpl(`/api/${path}`, {
        signal: controller.signal,
        headers: { 'X-Nreact-Token': token, ...(writing ? { 'Content-Type': 'application/json' } : {}) },
        ...(writing ? { method: 'POST', body: JSON.stringify(payload) } : {}),
      })
      let data
      try { data = await response.json() } catch { throw new ApiError('The local server returned invalid JSON.' + (writing ? uncertain : ''), response.status) }
      if (!response.ok) throw new ApiError(typeof data?.error === 'string' ? data.error.slice(0, 1000) : `Request failed (${response.status}).`, response.status)
      return data
    } catch (reason) {
      if (reason instanceof ApiError) throw reason
      const message = controller.signal.aborted ? 'The local server request timed out.' : 'The local server could not be reached.'
      throw new ApiError(message + (writing ? uncertain : ' Retrying read-only updates automatically.'))
    } finally {
      clearTimeout(timer)
    }
  }
}
