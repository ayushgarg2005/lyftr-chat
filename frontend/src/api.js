const runtimeUrl = window.__APP_CONFIG__?.API_URL
export const API_URL = (runtimeUrl || import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

export function newSessionId() {
  const rand = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(16).slice(2) + Date.now().toString(16)
  return 's-' + rand.replace(/-/g, '').slice(0, 12)
}

function describeError(status, body) {
  if (status === 503) {
    return 'All providers are unavailable right now (every API key failed). Please try again in a moment.'
  }
  const detail = body?.detail
  if (status === 422) {
    if (Array.isArray(detail)) {
      const parts = detail.map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== 'body').join('.') : ''
        const msg = (d.msg || '').replace(/^Value error, /, '')
        return field ? `${field}: ${msg}` : msg
      })
      return 'Invalid request. ' + parts.join('; ')
    }
    return 'Invalid request. ' + (detail || '')
  }
  if (typeof detail === 'string') return `Error ${status}: ${detail}`
  return `Unexpected error (${status}).`
}

export async function sendMessage({ sessionId, message, systemPrompt, simulate }) {
  const headers = { 'Content-Type': 'application/json' }
  if (simulate) headers['X-Simulate-Failure'] = simulate

  const payload = { session_id: sessionId, message }
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt

  let res
  try {
    res = await fetch(`${API_URL}/chat`, { method: 'POST', headers, body: JSON.stringify(payload) })
  } catch {
    return { ok: false, error: `Could not reach the server at ${API_URL}. Is the backend running?` }
  }

  let body = null
  try {
    body = await res.json()
  } catch {
    // non-JSON body, handled below
  }

  if (!res.ok || !body) {
    return { ok: false, status: res.status, error: describeError(res.status, body) }
  }
  return { ok: true, data: body }
}

export async function fetchHealth() {
  try {
    const res = await fetch(`${API_URL}/health`)
    return { ok: res.ok, data: await res.json() }
  } catch {
    return { ok: false, data: null }
  }
}
