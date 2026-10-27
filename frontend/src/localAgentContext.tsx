import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { JudgeStatus, Language, RunResponse } from './types'

export type AgentStatus = 'connected' | 'connecting' | 'disconnected'

export interface LocalRunPayload {
  code: string
  language: 'c' | 'cpp'
  stdin?: string
  timeout_sec?: number
}

/**
 * One compile, then one execution per stdin payload - the local twin of the
 * server's `run_code_batch`, and what *Run Samples* uses so N sample cases cost
 * one local build instead of N.
 */
export interface LocalBatchRunPayload {
  code: string
  language: 'c' | 'cpp'
  /** One stdin payload per case; the answer keeps this order, one entry each. */
  stdin_list: string[]
  timeout_sec?: number
}

export interface LocalAgentContextValue {
  status: AgentStatus
  compilerInfo: string | null
  agentVersion: string | null
  examLocked: boolean
  runLocally: (payload: LocalRunPayload) => Promise<RunResponse>
  runBatchLocally: (payload: LocalBatchRunPayload) => Promise<RunResponse[]>
  checkConnection: () => void
  startExamLock: (ticket?: string, timeoutSec?: number) => Promise<{ locked: boolean; token: string; is_windows: boolean }>
  releaseExamLock: () => Promise<void>
  heartbeatExamLock: () => Promise<boolean>
}

const LocalAgentContext = createContext<LocalAgentContextValue | null>(null)

const WS_URL = 'ws://127.0.0.1:8765'
const RECONNECT_INTERVAL_MS = 6000
/** Client-side deadline for a single compile + run (the agent caps its own at 30s). */
const SINGLE_DEADLINE_MS = 25_000
/** Compile allowance added on top of a batch's per-case wall clock. */
const BATCH_COMPILE_ALLOWANCE_MS = 20_000
const BATCH_MIN_DEADLINE_MS = 45_000
const BATCH_MAX_DEADLINE_MS = 600_000

/**
 * Guards the pending map: a caller that fires requests faster than one local
 * compiler can answer them must grow a queue, not an unbounded map of promises.
 */
const MAX_LOCAL_PENDING_REQUESTS = 64

/** Statuses the agent may report (identical vocabulary to the server judge). */
const JUDGE_STATUSES: JudgeStatus[] = [
  'success',
  'compile_error',
  'time_limit_exceeded',
  'runtime_error',
  'internal_error',
]

type AgentMessage = Record<string, unknown>

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberOf(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function nextRequestId(): string {
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

/**
 * Deadline for a batch: one compile, then `cases` runs, so a fixed 25s would
 * abort a legitimate 6-case sample set on a slow laptop before the agent was
 * anywhere near done.
 */
function batchDeadlineMs(cases: number, timeoutSec: number): number {
  const perCase = Math.max(1, cases) * (Math.max(1, timeoutSec) + 2) * 1000
  return Math.min(BATCH_MAX_DEADLINE_MS, Math.max(BATCH_MIN_DEADLINE_MS, perCase + BATCH_COMPILE_ALLOWANCE_MS))
}

function statusOf(value: unknown): JudgeStatus {
  const known = (JUDGE_STATUSES as string[]).includes(typeof value === 'string' ? value : '')
  // A reply we cannot read is the judge's fault, not the student's program's.
  return known ? (value as JudgeStatus) : 'internal_error'
}

/**
 * Map one agent reply (a `result` frame or one entry of a `batch_result`) onto the
 * server's `RunResponse`, so the result panel cannot tell the two paths apart.
 *
 * `shared` carries the batch-level compile fields: a batch compiles once, so the
 * diagnostics live on the envelope while each case carries only its own run.
 */
function toRunResponse(
  source: AgentMessage,
  language: Language,
  shared: { compile_output: string; compile_time_ms: number } = { compile_output: '', compile_time_ms: 0 },
): RunResponse {
  const compileOutput = textOf(source.compile_output) || shared.compile_output
  const compileTimeMs =
    'compile_time_ms' in source ? numberOf(source.compile_time_ms) : shared.compile_time_ms
  return {
    status: statusOf(source.status),
    stdout: textOf(source.stdout),
    stderr: textOf(source.stderr),
    compile_output: compileOutput,
    exit_code: typeof source.exit_code === 'number' ? source.exit_code : null,
    execution_time_ms: numberOf(source.execution_time_ms),
    signal_name: typeof source.signal_name === 'string' ? source.signal_name : null,
    compile_time_ms: compileTimeMs,
    // The agent answers with the language it was handed, but the request knows it
    // better than a missing field ever would.
    language,
    timed_out: Boolean(source.timed_out),
    truncated:
      Boolean(source.truncated) ||
      Boolean(source.stdout_truncated) ||
      Boolean(source.stderr_truncated),
  }
}

/**
 * Map a `batch_result` envelope onto one `RunResponse` per requested case.
 *
 * A failed compile runs nothing and answers with `results: []`, yet every case did
 * fail that same build. Mirroring the server's `run_code_batch` - which returns
 * one `compile_error` per case - keeps the "one verdict per case" contract the
 * caller relies on instead of handing it a short list to guess about.
 */
function toBatchResponses(
  data: AgentMessage,
  language: Language,
  requestedCases: number,
): RunResponse[] {
  const shared = {
    compile_output: textOf(data.compile_output),
    compile_time_ms: numberOf(data.compile_time_ms),
  }
  const raw = Array.isArray(data.results) ? data.results : []
  const mapped = raw
    .filter((entry): entry is AgentMessage => Boolean(entry) && typeof entry === 'object')
    .map((entry) => toRunResponse(entry, language, shared))

  if (mapped.length > 0 || requestedCases <= 0 || data.status === 'ok') return mapped

  const failed: RunResponse = {
    status: 'compile_error',
    stdout: '',
    stderr: '',
    compile_output: shared.compile_output,
    exit_code: typeof data.exit_code === 'number' ? data.exit_code : 1,
    execution_time_ms: 0,
    signal_name: null,
    compile_time_ms: shared.compile_time_ms,
    language,
    timed_out: Boolean(data.timed_out),
    truncated: false,
  }
  return Array.from({ length: requestedCases }, () => ({ ...failed }))
}

/** One in-flight request; a reply only settles the entry whose kind matches it. */
type PendingRequest =
  | {
      kind: 'single'
      language: Language
      timer: number
      resolve: (result: RunResponse) => void
      reject: (error: Error) => void
    }
  | {
      kind: 'batch'
      language: Language
      /** Cases the caller asked for, so a failed compile can pad the answer. */
      cases: number
      timer: number
      resolve: (results: RunResponse[]) => void
      reject: (error: Error) => void
    }
  | {
      kind: 'exam'
      timer: number
      resolve: (data: any) => void
      reject: (error: Error) => void
    }

export function LocalAgentProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AgentStatus>('disconnected')
  const [compilerInfo, setCompilerInfo] = useState<string | null>(null)
  const [agentVersion, setAgentVersion] = useState<string | null>(null)
  const [examLocked, setExamLocked] = useState<boolean>(false)

  const wsRef = useRef<WebSocket | null>(null)
  const pendingRequests = useRef<Map<string, PendingRequest>>(new Map())
  const reconnectTimer = useRef<number | null>(null)

  /**
   * Settle one request exactly once: delete the entry first, so a duplicate or
   * late reply finds nothing to resolve, and drop the client deadline with it.
   */
  const settle = (reqId: string, deliver: (pending: PendingRequest) => void) => {
    const pending = pendingRequests.current.get(reqId)
    if (!pending) return
    pendingRequests.current.delete(reqId)
    window.clearTimeout(pending.timer)
    deliver(pending)
  }

  const connect = () => {
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return
    }

    try {
      setStatus('connecting')
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        setStatus('connected')
        // Send ping to retrieve compiler info
        ws.send(JSON.stringify({ type: 'ping' }))
      }

      ws.onmessage = (event) => {
        let data: AgentMessage
        try {
          const parsed = JSON.parse(event.data)
          data = parsed && typeof parsed === 'object' ? (parsed as AgentMessage) : {}
        } catch {
          return // Ignore malformed message
        }

        const type = data.type
        if (type === 'pong') {
          setCompilerInfo(textOf(data.compiler) || 'Unknown Compiler')
          setAgentVersion(textOf(data.version) || '1.0.0')
          return
        }

        const reqId = typeof data.id === 'string' ? data.id : ''
        if (!reqId || !pendingRequests.current.has(reqId)) return // late/foreign reply

        if (type === 'result' || type === 'batch_result') {
          // Answer the way the *request* asked to be answered: a batch reply to a
          // single request (or the reverse) is a protocol break, not a verdict.
          if (type === 'result') {
            settle(reqId, (pending) => {
              if (pending.kind === 'single') pending.resolve(toRunResponse(data, pending.language))
              else pending.reject(new Error('Local Agent answered a batch request with a single result.'))
            })
          } else {
            settle(reqId, (pending) => {
              if (pending.kind === 'batch') {
                pending.resolve(toBatchResponses(data, pending.language, pending.cases))
              } else {
                pending.reject(new Error('Local Agent answered a single request with a batch result.'))
              }
            })
          }
        } else if (
          type === 'exam_lock_started' ||
          type === 'exam_lock_status' ||
          type === 'exam_lock_released' ||
          type === 'exam_lock_heartbeat_ack'
        ) {
          if (type === 'exam_lock_started' || type === 'exam_lock_status') {
            setExamLocked(Boolean(data.locked))
          } else if (type === 'exam_lock_released') {
            setExamLocked(false)
          }
          settle(reqId, (pending) => {
            if (pending.kind === 'exam') pending.resolve(data)
          })
        } else if (type === 'error') {
          // The agent rejects a malformed request (e.g. a non-array stdin_list)
          // with this frame; without the reject the caller would sit on its
          // deadline for the whole 25s.
          settle(reqId, (pending) =>
            pending.reject(
              new Error(textOf(data.message) || 'The Local Agent rejected this request.'),
            ),
          )
        }
      }

      ws.onerror = () => {
        // Will trigger onclose
      }

      ws.onclose = () => {
        setStatus('disconnected')
        setCompilerInfo(null)
        setExamLocked(false)
        wsRef.current = null

        // Reject any pending local executions with fallback prompt
        pendingRequests.current.forEach(({ reject, timer }) => {
          window.clearTimeout(timer)
          reject(new Error('Local Agent connection lost mid-execution'))
        })
        pendingRequests.current.clear()

        // Schedule reconnect
        if (reconnectTimer.current === null) {
          reconnectTimer.current = window.setTimeout(() => {
            reconnectTimer.current = null
            connect()
          }, RECONNECT_INTERVAL_MS)
        }
      }
    } catch {
      setStatus('disconnected')
    }
  }

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [])

  /** The live socket, or `null` when the agent cannot be handed a request. */
  const openSocket = (): WebSocket | null => {
    const ws = wsRef.current
    return ws && ws.readyState === WebSocket.OPEN ? ws : null
  }

  /**
   * Compile + run one snippet on the student's machine (single case, `type: run`).
   *
   * Rejects - so callers can fall back to the server - when the agent is not
   * connected, when the local queue is full, when the agent answers with an
   * `error` frame, or when the client deadline expires.
   */
  const runLocally = (payload: LocalRunPayload): Promise<RunResponse> => {
    const ws = openSocket()
    if (!ws) return Promise.reject(new Error('Local Agent is not connected.'))
    if (pendingRequests.current.size >= MAX_LOCAL_PENDING_REQUESTS) {
      return Promise.reject(new Error('Too many local executions are already in flight.'))
    }

    const language = payload.language
    const reqId = nextRequestId()
    return new Promise<RunResponse>((resolve, reject) => {
      // Safety timeout on the client side: the agent caps its own run at 30s, so a
      // missing reply means this socket is not going to answer.
      const timer = window.setTimeout(() => {
        if (pendingRequests.current.delete(reqId)) {
          reject(new Error('Local execution timed out on client socket deadline.'))
        }
      }, SINGLE_DEADLINE_MS)
      pendingRequests.current.set(reqId, { kind: 'single', language, timer, resolve, reject })

      try {
        ws.send(
          JSON.stringify({
            type: 'run',
            id: reqId,
            code: payload.code,
            language,
            stdin: payload.stdin || '',
            timeout_sec: payload.timeout_sec || 3.0,
          }),
        )
      } catch (error) {
        if (pendingRequests.current.delete(reqId)) window.clearTimeout(timer)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  /**
   * One local compile, then one run per stdin payload (`type: run_batch`).
   *
   * Resolves with one `RunResponse` per entry of `payload.stdin_list`, in the same
   * order, so the caller can `zip` its cases with the verdicts; a failed compile
   * yields one `compile_error` per case rather than an empty list.
   */
  const runBatchLocally = (payload: LocalBatchRunPayload): Promise<RunResponse[]> => {
    const ws = openSocket()
    if (!ws) return Promise.reject(new Error('Local Agent is not connected.'))
    if (pendingRequests.current.size >= MAX_LOCAL_PENDING_REQUESTS) {
      return Promise.reject(new Error('Too many local executions are already in flight.'))
    }

    const stdinList = (payload.stdin_list || []).map((caseStdin) => caseStdin ?? '')
    if (stdinList.length === 0) return Promise.resolve([])

    const language = payload.language
    const timeoutSec = payload.timeout_sec || 3.0
    const reqId = nextRequestId()
    return new Promise<RunResponse[]>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (pendingRequests.current.delete(reqId)) {
          reject(new Error('Local batch execution timed out on client socket deadline.'))
        }
      }, batchDeadlineMs(stdinList.length, timeoutSec))
      pendingRequests.current.set(reqId, {
        kind: 'batch',
        language,
        cases: stdinList.length,
        timer,
        resolve,
        reject,
      })

      try {
        ws.send(
          JSON.stringify({
            type: 'run_batch',
            id: reqId,
            code: payload.code,
            language,
            stdin_list: stdinList,
            timeout_sec: timeoutSec,
          }),
        )
      } catch (error) {
        if (pendingRequests.current.delete(reqId)) window.clearTimeout(timer)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  const startExamLock = (ticket?: string, timeoutSec = 120): Promise<{ locked: boolean; token: string; is_windows: boolean }> => {
    const ws = openSocket()
    if (!ws) return Promise.reject(new Error('Local Agent is not connected.'))
    const reqId = nextRequestId()
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (pendingRequests.current.delete(reqId)) {
          reject(new Error('Agent lock request timed out.'))
        }
      }, 5000)
      pendingRequests.current.set(reqId, {
        kind: 'exam',
        timer,
        resolve: (data) => resolve({
          locked: Boolean(data.locked),
          token: String(data.token || ''),
          is_windows: Boolean(data.is_windows),
        }),
        reject,
      })
      ws.send(JSON.stringify({ type: 'start_exam_lock', id: reqId, timeout_sec: timeoutSec, ticket }))
    })
  }

  const releaseExamLock = (): Promise<void> => {
    const ws = openSocket()
    if (!ws) {
      setExamLocked(false)
      return Promise.resolve()
    }
    const reqId = nextRequestId()
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        pendingRequests.current.delete(reqId)
        setExamLocked(false)
        resolve()
      }, 3000)
      pendingRequests.current.set(reqId, {
        kind: 'exam',
        timer,
        resolve: () => {
          setExamLocked(false)
          resolve()
        },
        reject: () => {
          setExamLocked(false)
          resolve()
        },
      })
      ws.send(JSON.stringify({ type: 'release_exam_lock', id: reqId }))
    })
  }

  const heartbeatExamLock = (): Promise<boolean> => {
    const ws = openSocket()
    if (!ws) return Promise.resolve(false)
    const reqId = nextRequestId()
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        pendingRequests.current.delete(reqId)
        resolve(false)
      }, 3000)
      pendingRequests.current.set(reqId, {
        kind: 'exam',
        timer,
        resolve: (data) => resolve(Boolean(data.locked)),
        reject: () => resolve(false),
      })
      ws.send(JSON.stringify({ type: 'heartbeat_exam_lock', id: reqId }))
    })
  }

  return (
    <LocalAgentContext.Provider
      value={{
        status,
        compilerInfo,
        agentVersion,
        examLocked,
        runLocally,
        runBatchLocally,
        checkConnection: connect,
        startExamLock,
        releaseExamLock,
        heartbeatExamLock,
      }}
    >
      {children}
    </LocalAgentContext.Provider>
  )
}

export function useLocalAgent(): LocalAgentContextValue {
  const ctx = useContext(LocalAgentContext)
  if (!ctx) {
    throw new Error('useLocalAgent must be used within LocalAgentProvider')
  }
  return ctx
}
