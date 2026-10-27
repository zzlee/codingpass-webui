/**
 * CodingPass student workspace.
 *
 * Layout: judge toolbar on top, Monaco editor on the left (~58%), stdin +
 * judge output on the right. `Ctrl/Cmd + Enter` compiles and runs wherever the
 * focus currently is.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Editor, { type OnChange, type OnMount } from '@monaco-editor/react'
import { CodeXml, LoaderCircle } from 'lucide-react'

import './monacoSetup'
import { EDITOR_OPTIONS, EDITOR_THEME } from './monacoSetup'
import ConsolePanel from './components/ConsolePanel'
import Header from './components/Header'
import {
  DEFAULT_TEMPLATE_ID,
  findTemplateId,
  defaultSource,
  getTemplate,
  templatesFor,
  type TemplateId,
} from './templates'
import {
  LANGUAGE_LABEL,
  MONACO_LANGUAGE,
  SOURCE_EXTENSION,
  type BackendHealth,
  type HealthResponse,
  type Language,
  type RunRequest,
  type RunResponse,
} from './types'

/** Give up on a request that never answers (judge restart, network stall). */
const RUN_TIMEOUT_MS = 60_000
/** How often to re-probe `/api/health`. */
const HEALTH_POLL_MS = 20_000

const STARTING_SOURCES: Record<Language, string> = {
  c: defaultSource('c'),
  cpp: defaultSource('cpp'),
}

function describeRunFailure(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return `The judge did not answer within ${Math.round(RUN_TIMEOUT_MS / 1000)}s. Is the backend still busy?`
  }
  if (error instanceof TypeError) {
    return `Cannot reach the judge API: ${error.message}. Start it with: cd backend && ./venv/bin/uvicorn main:app --port 8010`
  }
  return error instanceof Error ? error.message : String(error)
}

/**
 * Everything a judge request needs, held in one object.
 *
 * A ref mirror of it (`workspaceRef`) is updated synchronously by every
 * mutation, so `Ctrl+Enter` pressed in the very same frame as the last
 * keystroke still submits the fresh buffer instead of the previous render's.
 */
interface Workspace {
  language: Language
  /** One source buffer per language: switching tabs never loses work. */
  sources: Record<Language, string>
  stdin: string
}

const INITIAL_WORKSPACE: Workspace = {
  language: 'c',
  sources: STARTING_SOURCES,
  stdin: getTemplate('c', DEFAULT_TEMPLATE_ID)!.stdin,
}

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace>(INITIAL_WORKSPACE)
  const workspaceRef = useRef<Workspace>(INITIAL_WORKSPACE)
  const runningRef = useRef(false)
  const [response, setResponse] = useState<RunResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [transportError, setTransportError] = useState<string | null>(null)
  const [health, setHealth] = useState<BackendHealth>({
    status: 'checking',
    detail: 'Contacting the judge…',
    info: null,
  })

  /** The single mutation entry point: keeps state and ref in lockstep. */
  const patch = useCallback((update: (previous: Workspace) => Workspace) => {
    const next = update(workspaceRef.current)
    if (next === workspaceRef.current) return
    workspaceRef.current = next
    setWorkspace(next)
  }, [])

  const { language, stdin } = workspace
  const code = workspace.sources[language]
  const templates = useMemo(() => templatesFor(language), [language])
  /** '' when the buffer no longer matches a stock template. */
  const activeTemplateId = useMemo(() => findTemplateId(language, code), [language, code])

  // ---------------------------------------------------------------- actions
  const runCode = useCallback(async () => {
    // Synchronous guard: two key presses in the same tick must not double-post.
    if (runningRef.current) return
    runningRef.current = true
    setRunning(true)
    setTransportError(null)

    const current = workspaceRef.current
    const request: RunRequest = {
      code: current.sources[current.language],
      language: current.language,
      stdin: current.stdin,
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), RUN_TIMEOUT_MS)

    try {
      const reply = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      })

      if (!reply.ok) {
        const body = await reply.text().catch(() => '')
        const trimmed = body.length > 400 ? `${body.slice(0, 400)}…` : body
        // 502/504 come from the Vite proxy when uvicorn is not listening.
        const proxyHint =
          reply.status === 502 || reply.status === 504
            ? ' - the dev proxy cannot reach the judge, is uvicorn running on :8000?'
            : ''
        throw new Error(
          `Judge responded with HTTP ${reply.status}${proxyHint}${trimmed ? `: ${trimmed}` : ''}`,
        )
      }

      setResponse((await reply.json()) as RunResponse)
    } catch (error) {
      setResponse(null)
      setTransportError(describeRunFailure(error))
    } finally {
      window.clearTimeout(timer)
      runningRef.current = false
      setRunning(false)
    }
  }, [])

  /** Load a starter template (and its sample stdin) into the current buffer. */
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

  const changeLanguage = useCallback(
    (next: Language) => {
      patch((previous) => ({ ...previous, language: next }))
      setResponse(null)
      setTransportError(null)
    },
    [patch],
  )

  const probeHealth = useCallback(async () => {
    try {
      const reply = await fetch('/api/health')
      if (!reply.ok) throw new Error(`HTTP ${reply.status}`)
      const info = (await reply.json()) as HealthResponse
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
  const checkHealth = useCallback(async () => {
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

  // `Ctrl/Cmd + Enter` runs the submission from anywhere, including inside the
  // editor (the capture-phase listener consumes the event before Monaco sees it).
  // `runCode` is stable and reads the workspace ref, so it is never stale.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || (!event.ctrlKey && !event.metaKey)) return
      if (event.altKey || event.shiftKey) return
      event.preventDefault()
      event.stopPropagation()
      void runCode()
    }
    window.addEventListener('keydown', onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true })
  }, [runCode])

  const handleEditorChange: OnChange = useCallback(
    (value) => {
      const next = value ?? ''
      patch((previous) =>
        previous.sources[previous.language] === next
          ? previous
          : { ...previous, sources: { ...previous.sources, [previous.language]: next } },
      )
    },
    [patch],
  )

  const handleStdinChange = useCallback(
    (value: string) => {
      patch((previous) => ({ ...previous, stdin: value }))
    },
    [patch],
  )

  const handleEditorMount: OnMount = useCallback((instance) => {
    instance.focus()
  }, [])

  const sourceName = `solution.${SOURCE_EXTENSION[language]}`
  const lineCount = code.split('\n').length
  const memoryLimitMb = health.info?.limits.memory_limit_bytes
    ? Math.round(health.info.limits.memory_limit_bytes / (1024 * 1024))
    : null
  const timeoutSeconds = health.info?.limits.timeout_seconds ?? null

  return (
    <div className="flex h-screen min-h-0 flex-col bg-slate-950 font-sans text-slate-200">
      <Header
        language={language}
        onLanguageChange={changeLanguage}
        templates={templates}
        templateId={activeTemplateId}
        onTemplateChange={applyTemplate}
        running={running}
        onRun={() => void runCode()}
        health={health}
        onHealthCheck={() => void checkHealth()}
      />

      <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 lg:flex-row lg:gap-4 lg:overflow-hidden lg:p-4">
        {/* Editor ------------------------------------------------------- */}
        <section className="flex min-h-[24rem] flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 lg:min-h-0 lg:basis-[58%]">
          <div className="flex shrink-0 items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-2">
            <CodeXml className="h-4 w-4 text-sky-400" aria-hidden="true" />
            <span className="font-mono text-xs text-slate-300">{sourceName}</span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
              {LANGUAGE_LABEL[language]}
            </span>
            <span className="hidden font-mono text-[10px] text-slate-600 sm:inline">
              {lineCount} line{lineCount === 1 ? '' : 's'}
            </span>
            <span className="ml-auto hidden truncate text-[11px] text-slate-500 md:inline" title={activeTemplateId ? 'Starter template loaded' : 'Your own code'}>
              {templates.find((template) => template.id === activeTemplateId)?.description ??
                'Write your solution here'}
            </span>
          </div>

          <div className="min-h-0 flex-1">
            <Editor
              path={`file:///solution.${SOURCE_EXTENSION[language]}`}
              language={MONACO_LANGUAGE[language]}
              value={code}
              theme={EDITOR_THEME}
              onChange={handleEditorChange}
              onMount={handleEditorMount}
              options={EDITOR_OPTIONS}
              loading={
                <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                  <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Loading editor…
                </div>
              }
            />
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-800/80 bg-slate-900/70 px-3 py-1.5 font-mono text-[10px] text-slate-500">
            <span>{timeoutSeconds ? `timeout ${timeoutSeconds}s` : 'timeout 3s'}</span>
            <span className="text-slate-700">|</span>
            <span>{memoryLimitMb ? `memory ${memoryLimitMb}MB` : 'memory 256MB'}</span>
            <span className="text-slate-700">|</span>
            <span>{language === 'c' ? 'gcc -O2 -Wall -lm' : 'g++ -O2 -std=c++17 -Wall -lm'}</span>
            <span className="ml-auto text-slate-600">Ctrl/Cmd + Enter to run</span>
          </div>
        </section>

        {/* Console ------------------------------------------------------ */}
        <section className="flex min-h-[20rem] min-w-0 flex-col lg:min-h-0 lg:basis-[42%]">
          <ConsolePanel
            stdin={stdin}
            onStdinChange={handleStdinChange}
            response={response}
            running={running}
            transportError={transportError}
          />
        </section>
      </main>
    </div>
  )
}
