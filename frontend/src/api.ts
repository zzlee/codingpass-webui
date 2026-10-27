/**
 * Typed client for the CodingPass FastAPI judge (`backend/main.py`).
 *
 * Every call goes to the relative `/api` prefix, which Vite proxies to the
 * judge (see `vite.config.ts`), so the browser never needs CORS in development.
 *
 * The bearer token lives in `localStorage` under {@link TOKEN_STORAGE_KEY}; the
 * auth provider writes it and this module reads it, so any component can call
 * the API without prop drilling. A `401` on an authenticated call triggers the
 * {@link setUnauthorizedHandler} callback, which logs the user out once instead
 * of leaving a stale token in every panel.
 */
import type {
  Classroom,
  ClassroomDetail,
  Difficulty,
  HealthResponse,
  ProblemCreateRequest,
  ProblemDetail,
  ProblemListResponse,
  RegisterRequest,
  RunRequest,
  RunResponse,
  SubmissionListResponse,
  SubmissionResult,
  TestCaseDraft,
  TokenResponse,
  User,
  Verdict,
} from './types'

/** localStorage key holding the JWT. */
export const TOKEN_STORAGE_KEY = 'codingpass.token'

/** Optional storage for the expiry timestamp (seconds since epoch, as text). */
const EXPIRES_AT_KEY = 'codingpass.token.expires_at'

/** Read the persisted bearer token, or `null` when nobody is signed in. */
export function readStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    // Private mode / disabled storage: the session simply stays in memory.
    return null
  }
}

/** Persist (or clear, with `null`) the bearer token and its expiry. */
export function storeToken(token: string | null, expiresInSec = 0): void {
  try {
    if (token === null) {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY)
      window.localStorage.removeItem(EXPIRES_AT_KEY)
      return
    }
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
    window.localStorage.setItem(
      EXPIRES_AT_KEY,
      String(Date.now() + Math.max(0, expiresInSec) * 1000),
    )
  } catch {
    /* ignore quota / disabled storage */
  }
}

/** True when the stored token is gone or known to be expired. */
export function storedTokenIsUsable(): boolean {
  const token = readStoredToken()
  if (!token) return false
  const raw = safeGet(EXPIRES_AT_KEY)
  if (!raw) return true
  const expiresAt = Number(raw)
  // Re-authenticate 30 s early so a click never races the expiry.
  return Number.isFinite(expiresAt) ? Date.now() < expiresAt - 30_000 : true
}

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

/** API failure with the FastAPI `detail` already unwrapped. */
export class ApiError extends Error {
  status: number
  /** Human readable one-liner taken from the response body. */
  detail: string
  /** Raw `detail` payload (a string, or the list of 422 validation errors). */
  raw: unknown

  constructor(status: number, detail: string, raw: unknown, fallback: string) {
    super(detail || fallback)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.raw = raw
  }
}

/** Flask/FastAPI-style `detail` -> single line, including 422 field errors. */
function flattenDetail(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>
    const detail = record.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail)) {
      // Pydantic validation errors: [{loc: ['body','title'], msg: '...', type}]
      const parts = detail
        .map((entry) => {
          if (!entry || typeof entry !== 'object') return String(entry)
          const item = entry as Record<string, unknown>
          const loc = Array.isArray(item.loc) ? item.loc : []
          const field = loc.filter((part) => part !== 'body').join('.')
          const msg = typeof item.msg === 'string' ? item.msg : JSON.stringify(item)
          return field ? `${field}: ${msg}` : msg
        })
        .filter(Boolean)
      if (parts.length) return parts.slice(0, 4).join(' · ')
    }
  }
  if (typeof body === 'string' && body.trim()) {
    return body.length > 300 ? `${body.slice(0, 300)}…` : body
  }
  return statusText(status)
}

function statusText(status: number): string {
  if (status === 401) return 'Your session has expired - please sign in again.'
  if (status === 403) return 'You are not allowed to do that.'
  if (status === 404) return 'The server has no such record.'
  if (status === 409) return 'That conflicts with an existing record.'
  if (status === 422) return 'The form did not pass validation.'
  if (status === 503) return 'The judge is unavailable right now.'
  return `Request failed with HTTP ${status}.`
}

type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

/** Register the callback fired when the API rejects our token. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** JSON body; omit for bodyless requests. */
  body?: unknown
  /** Send `Authorization` when a token exists (default `true`). */
  auth?: boolean
  signal?: AbortSignal
  /** Client side deadline in ms; 0 disables it. Default 30 s. */
  timeoutMs?: number
}

/**
 * Perform a request and parse the JSON response.
 *
 * @throws {ApiError} on a non-2xx status, transport failure or timeout.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, signal, timeoutMs = 30_000 } = options

  const controller = new AbortController()
  const timer = timeoutMs > 0 ? window.setTimeout(() => controller.abort(), timeoutMs) : null
  const onOuterAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onOuterAbort, { once: true })
  }

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = auth ? readStoredToken() : null
  if (token) headers.Authorization = `Bearer ${token}`

  try {
    const reply = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })

    if (!reply.ok) {
      const parsed = await reply.clone().json().catch(() => null)
      const detail = flattenDetail(parsed ?? (await reply.text().catch(() => '')), reply.status)
      // A rejected/expired token must clear the session exactly once; login
      // failures (401 without a token) must not log anybody out.
      if (reply.status === 401 && token && unauthorizedHandler) unauthorizedHandler()
      throw new ApiError(reply.status, detail, parsed, statusText(reply.status))
    }

    if (reply.status === 204) return undefined as T
    return (await reply.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const aborted = error instanceof DOMException && error.name === 'AbortError'
    if (aborted && signal?.aborted) throw new ApiError(0, 'The request was cancelled.', null, '')
    if (aborted) {
      throw new ApiError(
        0,
        `The server did not answer within ${Math.round(timeoutMs / 1000)}s. ` +
          'Is the judge still busy, or is it offline?',
        null,
        '',
      )
    }
    throw new ApiError(
      0,
      `Cannot reach the CodingPass API: ${error instanceof Error ? error.message : String(error)}. ` +
        'Start it with: cd backend && ./venv/bin/uvicorn main:app --port 8010',
      null,
      '',
    )
  } finally {
    if (timer !== null) window.clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', onOuterAbort)
  }
}

/** Query-string builder that skips empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const line = search.toString()
  return line ? `?${line}` : ''
}

/** Filters accepted by `GET /api/problems`. */
export interface ProblemQuery {
  classroom_id?: number
  difficulty?: Difficulty | ''
  mine?: boolean
  limit?: number
  offset?: number
}

/** Filters accepted by `GET /api/submissions`. */
export interface SubmissionQuery {
  problem_id?: number
  classroom_id?: number
  student_id?: number
  status?: Verdict | ''
  limit?: number
  offset?: number
}

/** Every endpoint the WebUI talks to, in one namespaced object. */
export const api = {
  // -- accounts ---------------------------------------------------------
  login: (identifier: string, password: string) =>
    request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: { username: identifier, password },
      auth: false,
    }),

  register: (payload: RegisterRequest) =>
    request<User>('/auth/register', { method: 'POST', body: payload, auth: false }),

  me: () => request<User>('/auth/me'),

  // -- classrooms -------------------------------------------------------
  listClassrooms: () => request<Classroom[]>('/classrooms'),

  createClassroom: (payload: { name: string; description?: string; code?: string | null }) =>
    request<Classroom>('/classrooms', { method: 'POST', body: payload }),

  joinClassroom: (code: string) =>
    request<ClassroomDetail>('/classrooms/join', { method: 'POST', body: { code } }),

  getClassroom: (id: number) => request<ClassroomDetail>(`/classrooms/${id}`),

  // -- problems ---------------------------------------------------------
  listProblems: (query: ProblemQuery = {}) =>
    request<ProblemListResponse>(`/problems${qs({ ...query })}`),

  getProblem: (id: number) => request<ProblemDetail>(`/problems/${id}`),

  createProblem: (payload: ProblemCreateRequest) =>
    request<ProblemDetail>('/problems', { method: 'POST', body: payload }),

  addTestCase: (problemId: number, payload: TestCaseDraft) =>
    request<ProblemDetail>(`/problems/${problemId}/testcases`, { method: 'POST', body: payload }),

  // -- judging ----------------------------------------------------------
  /** Free-form playground run. Public endpoint, deliberately sent without auth. */
  run: (payload: RunRequest, timeoutMs: number) =>
    request<RunResponse>('/run', { method: 'POST', body: payload, auth: false, timeoutMs }),

  submit: (problemId: number, language: 'c' | 'cpp', code: string, timeoutMs: number) =>
    request<SubmissionResult>(`/problems/${problemId}/submit`, {
      method: 'POST',
      body: { language, code },
      timeoutMs,
    }),

  // -- history ----------------------------------------------------------
  listSubmissions: (query: SubmissionQuery = {}) =>
    request<SubmissionListResponse>(`/submissions${qs({ ...query })}`),

  health: () => request<HealthResponse>('/health', { auth: false }),
}

export default api
