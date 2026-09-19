/**
 * Fetch wrapper for the Django REST API.
 *
 * Same-origin session auth, so the only ceremony is echoing the CSRF cookie
 * back on unsafe methods.
 */

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE'])

export class ApiError extends Error {
  constructor(message, { status, data } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }

  /** Field errors from a DRF serializer, flattened for display. */
  get fieldErrors() {
    if (!this.data || typeof this.data !== 'object') return []
    return Object.entries(this.data)
      .filter(([key]) => key !== 'detail')
      .map(([key, value]) => `${key}: ${[].concat(value).join(', ')}`)
  }
}

function readCookie(name) {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`))
  return match ? decodeURIComponent(match[2]) : null
}

async function request(path, { method = 'GET', body, params, raw = false } = {}) {
  const url = new URL(path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value)
      }
    })
  }

  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (!SAFE_METHODS.has(method)) {
    const token = readCookie('csrftoken')
    if (token) headers['X-CSRFToken'] = token
  }

  let response
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (cause) {
    throw new ApiError('Cannot reach the server. Check your connection.', { status: 0 })
  }

  if (raw) {
    if (!response.ok) throw new ApiError(`Request failed (${response.status})`, { status: response.status })
    return response
  }

  if (response.status === 204) return null

  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text.slice(0, 300) }
    }
  }

  if (!response.ok) {
    const detail =
      data?.detail ||
      (Array.isArray(data?.non_field_errors) ? data.non_field_errors[0] : null) ||
      `Request failed (${response.status})`
    throw new ApiError(detail, { status: response.status, data })
  }

  return data
}

export const api = {
  get: (path, params) => request(path, { params }),
  post: (path, body) => request(path, { method: 'POST', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  raw: (path, params) => request(path, { params, raw: true }),
}

/** DRF pagination returns {results: []}; plain lists come back bare. */
export const listOf = (payload) =>
  Array.isArray(payload) ? payload : (payload?.results ?? [])

export default api
