/**
 * Tab 1 - the free-form Playground: Monaco on the left, stdin + judge output on
 * the right.
 *
 * All state lives in `<WorkspaceProvider />`, so leaving this tab and coming
 * back restores the buffers, the sample stdin and the last judge result exactly
 * as the student left them. `Ctrl/Cmd + Enter` is claimed here through
 * `useCtrlEnter`, which is why running works from inside the editor, from the
 * stdin box, or with nothing focused at all.
 */
import { useCallback, useMemo, useRef } from 'react'
import Editor, { type OnChange, type OnMount } from '@monaco-editor/react'
import { CodeXml, Eraser, LoaderCircle, Play } from 'lucide-react'

import '../monacoSetup'
import { EDITOR_OPTIONS, EDITOR_THEME } from '../monacoSetup'
import AITutorPanel from './AITutorPanel'
import ConsolePanel from './ConsolePanel'
import { Segmented } from './ui'
import { useCtrlEnter } from '../shortcuts'
import { useWorkspace } from '../workspaceContext'
import { useLocalAgent } from '../localAgentContext'
import { api } from '../api'
import {
  findTemplateId,
  templatesFor,
  type TemplateId,
} from '../templates'
import {
  LANGUAGE_LABEL,
  MONACO_LANGUAGE,
  SOURCE_EXTENSION,
  type Language,
} from '../types'

const LANGUAGE_OPTIONS: Array<{ value: Language; label: string; hint: string }> = [
  { value: 'c', label: 'C', hint: 'gcc -O2 -Wall' },
  { value: 'cpp', label: 'C++', hint: 'g++ -O2 -std=c++17' },
]

export function Playground() {
  const {
    language,
    code,
    stdin,
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
  } = useWorkspace()

  const localAgent = useLocalAgent()

  // Synchronous double-press guard on top of the provider's own guard.
  const pressedRef = useRef(false)
  const templates = useMemo(() => templatesFor(language), [language])
  /** '' when the buffer no longer matches a stock template. */
  const activeTemplateId = useMemo(() => findTemplateId(language, code), [language, code])

  const handleRun = useCallback(async () => {
    if (pressedRef.current) return
    pressedRef.current = true
    try {
      await runCode(async (body) => {
        if (localAgent.status === 'connected') {
          try {
            return await localAgent.runLocally(body)
          } catch (agentErr) {
            console.warn('Local agent run failed, falling back to server judge:', agentErr)
            return await api.run(body, 20_000)
          }
        }
        return await api.run(body, 20_000)
      })
    } finally {
      pressedRef.current = false
    }
  }, [runCode, localAgent])

  useCtrlEnter(() => {
    void handleRun()
  })

  const handleEditorChange: OnChange = useCallback(
    (value) => setCode(value ?? ''),
    [setCode],
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
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 lg:flex-row lg:gap-4 lg:overflow-hidden lg:p-4">
      {/* Editor ---------------------------------------------------------- */}
      <section className="flex min-h-[24rem] flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 lg:min-h-0 lg:basis-[58%]">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-2">
          <CodeXml className="h-4 w-4 text-sky-400" aria-hidden="true" />
          <span className="font-mono text-xs text-slate-300">{sourceName}</span>
          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
            {LANGUAGE_LABEL[language]}
          </span>
          <span className="hidden font-mono text-[10px] text-slate-600 sm:inline">
            {lineCount} line{lineCount === 1 ? '' : 's'}
          </span>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Segmented
              options={LANGUAGE_OPTIONS}
              value={language}
              onChange={setLanguage}
              ariaLabel="Playground language"
              size="sm"
            />
            <label className="relative flex items-center">
              <span className="sr-only">Load starter template</span>
              <select
                value={activeTemplateId}
                onChange={(event) => {
                  const next = event.target.value as TemplateId
                  if (next) applyTemplate(next)
                }}
                className="w-[13rem] max-w-[42vw] appearance-none rounded-lg border border-slate-700/80 bg-slate-900/80 py-1.5 pl-2.5 pr-7 text-xs text-slate-200 transition-colors hover:border-slate-600 focus:border-sky-500 focus:outline-none"
              >
                <option value="" disabled>
                  Custom code
                </option>
                {templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={resetPlayground}
              title="Reload the stock starter code for both languages"
              className="rounded-lg border border-slate-700 bg-slate-900/70 p-1.5 text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-200"
            >
              <Eraser className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">Reset Playground</span>
            </button>
            <button
              type="button"
              onClick={() => void handleRun()}
              disabled={running}
              title={
                localAgent.status === 'connected'
                  ? '由本機 CodingPass Local Agent 即時編譯執行 (Ctrl+Enter / Cmd+Enter)'
                  : 'Compile and run your submission (Ctrl+Enter / Cmd+Enter)'
              }
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 shadow-lg shadow-emerald-950/40 transition-all hover:bg-emerald-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {running ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Play className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true" />
              )}
              <span>{running ? 'Compiling…' : localAgent.status === 'connected' ? 'Run Code (Local)' : 'Run Code'}</span>
            </button>
          </div>
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

      {/* Console + AI tutor ---------------------------------------------- */}
      <section className="flex min-h-[20rem] min-w-0 flex-col gap-3 lg:min-h-0 lg:basis-[42%]">
        <ConsolePanel
          stdin={stdin}
          onStdinChange={setStdin}
          response={response}
          running={running}
          transportError={transportError}
        />
        <AITutorPanel
          code={code}
          language={language}
          status={response?.status}
          compileOutput={response?.compile_output}
          stderr={response?.stderr}
          compact
        />
      </section>
    </div>
  )
}

/** Template description shown next to the editor tab, for the empty state copy. */
export default Playground
