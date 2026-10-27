import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { RunResponse } from './types'

export type AgentStatus = 'connected' | 'connecting' | 'disconnected'

export interface LocalRunPayload {
  code: string
  language: 'c' | 'cpp'
  stdin?: string
  timeout_sec?: number
}

export interface LocalAgentContextValue {
  status: AgentStatus
  compilerInfo: string | null
  agentVersion: string | null
  runLocally: (payload: LocalRunPayload) => Promise<RunResponse>
  checkConnection: () => void
}

const LocalAgentContext = createContext<LocalAgentContextValue | null>(null)

const WS_URL = 'ws://127.0.0.1:8765'
const RECONNECT_INTERVAL_MS = 6000

export function LocalAgentProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AgentStatus>('disconnected')
  const [compilerInfo, setCompilerInfo] = useState<string | null>(null)
  const [agentVersion, setAgentVersion] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const pendingRequests = useRef<
    Map<string, { resolve: (res: RunResponse) => void; reject: (err: Error) => void }>
  >(new Map())
  const reconnectTimer = useRef<number | null>(null)

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
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'pong') {
            setCompilerInfo(data.compiler || 'Unknown Compiler')
            setAgentVersion(data.version || '1.0.0')
          } else if (data.type === 'result') {
            const reqId = data.id
            const pending = pendingRequests.current.get(reqId)
            if (pending) {
              pendingRequests.current.delete(reqId)
              const runResp: RunResponse = {
                status: data.status,
                stdout: data.stdout || '',
                stderr: data.stderr || '',
                compile_output: data.compile_output || '',
                exit_code: data.exit_code ?? 0,
                execution_time_ms: data.execution_time_ms ?? 0,
                signal_name: null,
                compile_time_ms: data.compile_time_ms ?? 0,
                language: 'cpp',
                timed_out: data.timed_out ?? false,
                truncated: false,
              }
              pending.resolve(runResp)
            }
          }
        } catch {
          // Ignore malformed message
        }
      }

      ws.onerror = () => {
        // Will trigger onclose
      }

      ws.onclose = () => {
        setStatus('disconnected')
        setCompilerInfo(null)
        wsRef.current = null

        // Reject any pending local executions with fallback prompt
        pendingRequests.current.forEach(({ reject }) => {
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

  const runLocally = (payload: LocalRunPayload): Promise<RunResponse> => {
    return new Promise((resolve, reject) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error('Local Agent is not connected.'))
        return
      }

      const reqId = `req-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
      pendingRequests.current.set(reqId, { resolve, reject })

      ws.send(
        JSON.stringify({
          type: 'run',
          id: reqId,
          code: payload.code,
          language: payload.language,
          stdin: payload.stdin || '',
          timeout_sec: payload.timeout_sec || 3.0,
        }),
      )

      // Safety timeout on client side: 25s
      setTimeout(() => {
        if (pendingRequests.current.has(reqId)) {
          pendingRequests.current.delete(reqId)
          reject(new Error('Local execution timed out on client socket deadline.'))
        }
      }, 25_000)
    })
  }

  return (
    <LocalAgentContext.Provider
      value={{
        status,
        compilerInfo,
        agentVersion,
        runLocally,
        checkConnection: connect,
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
