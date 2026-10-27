/**
 * Shared workspace state: the free-form Playground buffers, judge health polling
 * and unsaved Problem Bank solutions.
 *
 * This provider exists so switching tabs never loses work. The Playground is
 * unmounted while another tab is visible (a hidden Monaco instance would keep
 * repainting for nothing), so its buffers, last judge result and the drafts of
 * every problem the student is working on live here instead of in the view.
 *
 * It also owns the single `Ctrl/Cmd + Enter` listener: whichever view is mounted
 * last owns the shortcut (`useCtrlEnter` in `shortcuts.ts`), so in the Playground
 * it runs the code and in the Problem Solver it runs the sample cases.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import api from './api'
import { firePrimaryShortcut } from './shortcuts'
import {
  DEFAULT_TEMPLATE_ID,
  defaultSource,
  getTemplate,
  type TemplateId,
} from './templates'
import type { BackendHealth, Language, RunRequest, RunResponse } from './types'

/** Give up on a run that never answers (judge restart, network stall). */
const RUN_TIMEOUT_MS = 60_000
/** How often to re-probe `/api/health`. */
const HEALTH_POLL_MS = 20_000

/* -------------------------------------------------------------------------- */
/* Unsaved solutions per problem                                              */
/* -------------------------------------------------------------------------- */

/** What the solver keeps in the editor for one problem. */
export interface SolutionDraft {
  language: Language
  /** One buffer per language, so switching never loses work. */
  sources: Record<Language, string>
}

/* -------------------------------------------------------------------------- */
/* Playground workspace                                                        */
/* -------------------------------------------------------------------------- */

interface WorkspaceState {
  language: Language
  /** One source buffer per language: switching tabs never loses work. */
  sources: Record<Language, string>
  stdin: string
}

const INITIAL_WORKSPACE: WorkspaceState = {
  language: 'c',
  sources: { c: defaultSource('c'), cpp: defaultSource('cpp') },
  stdin: getTemplate('c', DEFAULT_TEMPLATE_ID)?.stdin ?? '',
}

interface WorkspaceContextValue extends WorkspaceState {
  /** Source of the currently selected language. */
  code: string
  setLanguage: (language: Language) => void
  /** Replace the buffer of the currently selected language. */
  setCode: (code: string) => void
  setStdin: (stdin: string) => void
  /** Load a starter template into the current buffer (and its sample stdin). */
  applyTemplate: (id: TemplateId) => void
  /** Reset the Playground buffers back to the stock templates. */
  resetPlayground: () => void

  // Playground judging -----------------------------------------------------
  response: RunResponse | null
  running: boolean
  transportError: string | null
  runCode: (customRunner?: (req: RunRequest) => Promise<RunResponse>) => Promise<void>

  // Judge liveness ---------------------------------------------------------
  health: BackendHealth
  checkHealth: () => Promise<void>

  // Problem Bank drafts ----------------------------------------------------
  getDraft: (problemId: number) => SolutionDraft | undefined
  setDraft: (problemId: number, draft: SolutionDraft) => void
  clearDraft: (problemId: number) => void
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

function describeRunFailure(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return `The judge did not answer within ${Math.round(RUN_TIMEOUT_MS / 1000)}s. Is the backend still busy?`
  }
  if (error instanceof TypeError) {
    return `Cannot reach the judge API: ${error.message}. Start it with: cd backend && ./venv/bin/uvicorn main:app --port 8010`
  }
  return error instanceof Error ? error.message : String(error)
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspace, setWorkspace] = useState<WorkspaceState>(INITIAL_WORKSPACE)
  // Ref mirror updated synchronously, so a `Ctrl+Enter` fired in the same frame
  // as the last keystroke judges the fresh buffer, not the previous render's.
  const workspaceRef = useRef<WorkspaceState>(INITIAL_WORKSPACE)
  const runningRef = useRef(false)
  const draftsRef = useRef(new Map<number, SolutionDraft>())

  const [response, setResponse] = useState<RunResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [transportError, setTransportError] = useState<string | null>(null)
  const [health, setHealth] = useState<BackendHealth>({
    status: 'checking',
    detail: 'Contacting the judge…',
    info: null,
  })

  /** The single mutation entry point: keeps state and ref in lockstep. */
  const patch = useCallback((update: (previous: WorkspaceState) => WorkspaceState) => {
    const next = update(workspaceRef.current)
    if (next === workspaceRef.current) return
    workspaceRef.current = next
    setWorkspace(next)
  }, [])

  const setLanguage = useCallback(
    (language: Language) => {
      patch((previous) => ({ ...previous, language }))
      setResponse(null)
      setTransportError(null)
    },
    [patch],
  )

  const setCode = useCallback(
    (code: string) => {
      patch((previous) =>
        previous.sources[previous.language] === code
          ? previous
          : { ...previous, sources: { ...previous.sources, [previous.language]: code } },
      )
    },
    [patch],
  )

  const setStdin = useCallback(
    (stdin: string) => {
      patch((previous) => (previous.stdin === stdin ? previous : { ...previous, stdin }))
    },
    [patch],
  )

  const applyTemplate = useCallback(
    (id: TemplateId) => {
      patch((previous) => {
        const template = getTemplate(previous.language, id)
        if (!template) return previous
        return {
          ...previous,
          sources: { ...previous.sources, [previous.language]: template.code },
          stdin: template.stdin === '' ? previous.stdin : template.stdin,
        }
      })
    },
    [patch],
  )

  const resetPlayground = useCallback(() => {
    patch(() => ({
      language: workspaceRef.current.language,
      sources: { c: defaultSource('c'), cpp: defaultSource('cpp') },
      stdin: getTemplate(workspaceRef.current.language, DEFAULT_TEMPLATE_ID)?.stdin ?? '',
    }))
    setResponse(null)
    setTransportError(null)
  }, [patch])

  const runCode = useCallback(async (customRunner?: (req: RunRequest) => Promise<RunResponse>): Promise<void> => {
    // Synchronous guard: two key presses in the same tick must not double-post.
    if (runningRef.current) return
    runningRef.current = true
    setRunning(true)
    setTransportError(null)

    const current = workspaceRef.current
    const body: RunRequest = {
      code: current.sources[current.language],
      language: current.language,
      stdin: current.stdin,
    }

    try {
      const runner = customRunner ?? ((b: RunRequest) => api.run(b, RUN_TIMEOUT_MS))
      setResponse(await runner(body))
    } catch (error) {
      setResponse(null)
      setTransportError(describeRunFailure(error))
    } finally {
      runningRef.current = false
      setRunning(false)
    }
  }, [])

  const probeHealth = useCallback(async (): Promise<void> => {
    try {
      const info = await api.health()
      const toolchain = info['g++_version'] || info.gcc_version
      setHealth({
        status: info.status === 'ok' ? 'online' : 'degraded',
        detail:
          info.status === 'ok'
            ? `Judge reachable - ${info.gcc_version}\n${toolchain}`
            : `Judge is degraded: ${toolchain}`,
        info,
      })
    } catch (error) {
      setHealth({
        status: 'offline',
        detail: `Judge unreachable (${error instanceof Error ? error.message : String(error)}). Start it with: cd backend && ./venv/bin/uvicorn main:app --port 8010`,
        info: null,
      })
    }
  }, [])

  /** Manual re-check: show the transient "checking" state, then probe. */
  const checkHealth = useCallback(async (): Promise<void> => {
    setHealth((previous) => ({ ...previous, status: 'checking', detail: 'Contacting the judge…' }))
    await probeHealth()
  }, [probeHealth])

  // -------------------------------------------------------------- lifecycle
  useEffect(() => {
    // Probe after the first paint: Monaco bootstraps on mount and the health
    // pill is not worth delaying that for.
    const kickoff = window.setTimeout(() => void probeHealth(), 0)
    const timer = window.setInterval(() => void probeHealth(), HEALTH_POLL_MS)
    return () => {
      window.clearTimeout(kickoff)
      window.clearInterval(timer)
    }
  }, [probeHealth])

  // One global capture-phase listener: it consumes the chord before Monaco
  // sees it, so the shortcut works inside the editor, in a textarea or nowhere.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || (!event.ctrlKey && !event.metaKey)) return
      if (event.altKey || event.shiftKey) return
      if (!firePrimaryShortcut()) return
      event.preventDefault()
      event.stopPropagation()
    }
    window.addEventListener('keydown', onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true })
  }, [])

  // ---------------------------------------------------------------- drafts
  const getDraft = useCallback(
    (problemId: number): SolutionDraft | undefined => draftsRef.current.get(problemId),
    [],
  )
  const setDraft = useCallback((problemId: number, draft: SolutionDraft) => {
    draftsRef.current.set(problemId, draft)
  }, [])
  const clearDraft = useCallback((problemId: number) => {
    draftsRef.current.delete(problemId)
  }, [])

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      ...workspace,
      code: workspace.sources[workspace.language],
      setLanguage,
      setCode,
      setStdin,
      applyTemplate,
      resetPlayground,
      response,
      running,
      transportError,
      runCode,
      health,
      checkHealth,
      getDraft,
      setDraft,
      clearDraft,
    }),
    [
      workspace,
      setLanguage,
      setCode,
      setStdin,
      applyTemplate,
      resetPlayground,
      response,
      running,
      transportError,
      runCode,
      health,
      checkHealth,
      getDraft,
      setDraft,
      clearDraft,
    ],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

/** Access the shared workspace. Throws outside `<WorkspaceProvider />`. */
export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext)
  if (!context) throw new Error('useWorkspace must be used inside <WorkspaceProvider>')
  return context
}

export default WorkspaceProvider
