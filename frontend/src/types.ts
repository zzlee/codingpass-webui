/**
 * Wire types shared between the CodingPass student UI and the FastAPI judge
 * (`backend/main.py`). Keep them in sync with the Pydantic schemas there.
 */

/** Languages accepted by `POST /api/run`. */
export type Language = 'c' | 'cpp'

/** Terminal judge states reported in `RunResponse.status`. */
export type JudgeStatus =
  | 'success'
  | 'compile_error'
  | 'time_limit_exceeded'
  | 'runtime_error'
  | 'internal_error'

/** `POST /api/run` request body. */
export interface RunRequest {
  /** Complete source file to compile and run. */
  code: string
  /** Target language. */
  language: Language
  /** Text piped to the program on stdin. */
  stdin?: string
}

/** `POST /api/run` response body (HTTP 200 for every judged outcome). */
export interface RunResponse {
  status: JudgeStatus
  /** Bytes written to stdout by the submission. */
  stdout: string
  /** Bytes written to stderr by the submission. */
  stderr: string
  /** Compiler diagnostics; non-empty when `status === 'compile_error'`. */
  compile_output: string
  /** Process exit code, or `null` when the process never started / was killed. */
  exit_code: number | null
  /** Wall-clock execution time in milliseconds. */
  execution_time_ms: number
  /** Set when the process died from a signal, e.g. `SIGSEGV`. */
  signal_name: string | null
  /** Wall-clock compile time in milliseconds. */
  compile_time_ms: number
  /** Language the result belongs to. */
  language: string
  /** True when the wall-clock or CPU limit was hit. */
  timed_out: boolean
  /** True when stdout and/or stderr were truncated by the judge. */
  truncated: boolean
}

/** `GET /api/health` response body. */
export interface HealthResponse {
  status: 'ok' | 'degraded'
  gcc_version: string
  /** Serialised with a literal key because of the `+` in `g++`. */
  'g++_version': string
  languages: string[]
  limits: {
    timeout_seconds?: number
    compile_timeout_seconds?: number
    memory_limit_bytes?: number
    file_size_limit_bytes?: number
    max_output_bytes?: number
    languages?: string[]
  }
}

/** Client-side view of the judge API liveness. */
export type BackendStatus = 'checking' | 'online' | 'degraded' | 'offline'

/** Health state rendered by the header pill. */
export interface BackendHealth {
  status: BackendStatus
  /** Short human readable explanation (used as the pill tooltip). */
  detail: string
  /** Last successful `/api/health` payload, if any. */
  info: HealthResponse | null
}

/** File extension the judge compiles a language with. */
export const SOURCE_EXTENSION: Record<Language, string> = {
  c: 'c',
  cpp: 'cpp',
}

/** Human readable language names. */
export const LANGUAGE_LABEL: Record<Language, string> = {
  c: 'C',
  cpp: 'C++',
}

/** Monaco model id for a language (`c` and `cpp` are built into monaco). */
export const MONACO_LANGUAGE: Record<Language, string> = {
  c: 'c',
  cpp: 'cpp',
}

/** Format milliseconds for the stats tiles. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(value < 10 ? 2 : 1)} ms`
}
