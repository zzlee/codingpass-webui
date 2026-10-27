/**
 * Right hand side panel: standard input for the submission plus the judged
 * result (status pill, timing stats, stdout and compiler diagnostics).
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Braces,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  CodeXml,
  Copy,
  Cpu,
  Gauge,
  ListChecks,
  LoaderCircle,
  Terminal,
  TimerReset,
  Trash,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'

import { formatMs, type JudgeStatus, type RunResponse } from '../types'

interface StatusStyle {
  label: string
  Icon: LucideIcon
  /** Pill classes (border + background + text). */
  pill: string
  /** Accent used for the result panel border. */
  accent: string
  /** What happened, in one line. */
  hint: string
}

const STATUS_STYLES: Record<JudgeStatus, StatusStyle> = {
  success: {
    label: 'SUCCESS',
    Icon: CircleCheck,
    pill: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
    accent: 'border-emerald-500/30',
    hint: 'Compiled and exited with status 0.',
  },
  compile_error: {
    label: 'COMPILE ERROR',
    Icon: TriangleAlert,
    pill: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
    accent: 'border-rose-500/30',
    hint: 'The compiler rejected your source - open Diagnostics below.',
  },
  time_limit_exceeded: {
    label: 'TIME LIMIT EXCEEDED',
    Icon: TimerReset,
    pill: 'border-orange-500/40 bg-orange-500/15 text-orange-300',
    accent: 'border-orange-500/30',
    hint: 'Wall-clock limit reached; the whole process group was killed.',
  },
  runtime_error: {
    label: 'RUNTIME ERROR',
    Icon: CircleAlert,
    pill: 'border-yellow-500/40 bg-yellow-500/15 text-yellow-200',
    accent: 'border-yellow-500/30',
    hint: 'The binary crashed or returned a non-zero exit code.',
  },
  internal_error: {
    label: 'JUDGE ERROR',
    Icon: CircleSlash,
    pill: 'border-fuchsia-500/40 bg-fuchsia-500/15 text-fuchsia-300',
    accent: 'border-fuchsia-500/30',
    hint: 'The sandbox could not judge this submission. Try again.',
  },
}

type OutputTab = 'stdout' | 'diagnostics'

interface OutputTabDescriptor {
  id: OutputTab
  label: string
  /** Number of lines shown in the tab, rendered as a badge. */
  badge: number
}

function buildTabs(stdout: string, diagnostics: string): OutputTabDescriptor[] {
  return [
    { id: 'stdout', label: 'Standard Output', badge: lineCount(stdout) },
    { id: 'diagnostics', label: 'Diagnostics', badge: lineCount(diagnostics) },
  ]
}

/** Clipboard write with a fallback for non-secure contexts (http://LAN host). */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const scratch = document.createElement('textarea')
    scratch.value = text
    scratch.setAttribute('readonly', '')
    scratch.style.position = 'fixed'
    scratch.style.top = '-1000px'
    document.body.appendChild(scratch)
    scratch.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(scratch)
    return ok
  } catch {
    return false
  }
}

function lineCount(text: string): number {
  if (!text) return 0
  return text.replace(/\n$/, '').split('\n').length
}

function Stat({
  Icon,
  label,
  value,
  tone = 'text-slate-100',
}: {
  Icon: LucideIcon
  label: string
  value: string
  tone?: string
}) {
  return (
    <div className="min-w-0 flex-1 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="truncate">{label}</span>
      </div>
      <div className={`mt-0.5 truncate font-mono text-sm font-semibold ${tone}`}>{value}</div>
    </div>
  )
}

/** Dark, monospaced, scrollable output box with a copy action. */
function TerminalBox({
  text,
  prompt,
  tone,
  emptyHint,
}: {
  text: string
  prompt: string
  tone: 'stdout' | 'stderr'
  emptyHint: string
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (copyState === 'idle') return
    const timer = window.setTimeout(() => setCopyState('idle'), 1800)
    return () => window.clearTimeout(timer)
  }, [copyState])

  const textColor = tone === 'stdout' ? 'text-slate-100' : 'text-rose-200/90'

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-[#050914]">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-1.5">
        <span className="flex gap-1" aria-hidden="true">
          <span className="h-2 w-2 rounded-full bg-rose-500/70" />
          <span className="h-2 w-2 rounded-full bg-amber-400/70" />
          <span className="h-2 w-2 rounded-full bg-emerald-500/70" />
        </span>
        <span className="truncate font-mono text-[11px] text-slate-500">{prompt}</span>
        <button
          type="button"
          disabled={!text}
          onClick={async () => {
            setCopyState((await writeClipboard(text)) ? 'copied' : 'failed')
          }}
          title={text ? 'Copy this output to the clipboard' : 'Nothing to copy yet'}
          className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-slate-700/70 bg-slate-900 px-2 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-600 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {copyState === 'copied' ? (
            <CircleCheck className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
          ) : copyState === 'failed' ? (
            <TriangleAlert className="h-3.5 w-3.5 text-amber-400" aria-hidden="true" />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'}
        </button>
      </div>
      <pre
        className={`min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-[12.5px] leading-relaxed ${textColor}`}
        tabIndex={0}
      >
        {text || <span className="italic text-slate-600">{emptyHint}</span>}
      </pre>
    </div>
  )
}

export interface ConsolePanelProps {
  stdin: string
  onStdinChange: (value: string) => void
  response: RunResponse | null
  running: boolean
  /** Transport level failure (backend unreachable, HTTP error, timeout). */
  transportError: string | null
}

export function ConsolePanel({
  stdin,
  onStdinChange,
  response,
  running,
  transportError,
}: ConsolePanelProps) {
  const [choice, setChoice] = useState<{ run: RunResponse; tab: OutputTab } | null>(null)

  const diagnostics = useMemo(() => {
    if (!response) return ''
    return (
      [response.compile_output, response.stderr]
        .filter((part) => part && part.length > 0)
        // Compiler messages come first: they explain why nothing was produced.
        .join('\n')
    )
  }, [response])

  const status: JudgeStatus | null = response?.status ?? null
  const statusStyle = status ? STATUS_STYLES[status] : null
  const stdout = response?.stdout ?? ''

  // Compiler / crash output is what a student must read first after a failed
  // run, so the tab follows the outcome unless the student picked one manually
  // for *this* run (a new response object clears that choice).
  const autoTab: OutputTab =
    response && response.status !== 'success' && diagnostics !== '' ? 'diagnostics' : 'stdout'
  const tab = choice && choice.run === response ? choice.tab : autoTab

  const exitLabel = (() => {
    if (!response) return '-'
    if (response.signal_name) return `-${response.signal_name}`
    return response.exit_code === null ? '-' : String(response.exit_code)
  })()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* ---------------------------------------------------------------- */}
      {/* stdin                                                            */}
      {/* ---------------------------------------------------------------- */}
      <section className="flex shrink-0 flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
        <div className="flex items-center gap-2">
          <Braces className="h-4 w-4 text-sky-400" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-200">Standard Input</h2>
          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">stdin</span>
          <button
            type="button"
            onClick={() => onStdinChange('')}
            disabled={stdin.length === 0}
            title="Clear the input box"
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-slate-700/70 px-2 py-1 text-[11px] font-medium text-slate-400 transition-colors hover:border-rose-500/50 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-slate-700/70 disabled:hover:text-slate-400"
          >
            <Trash className="h-3.5 w-3.5" aria-hidden="true" />
            Clear
          </button>
        </div>
        <textarea
          value={stdin}
          onChange={(event) => onStdinChange(event.target.value)}
          spellCheck={false}
          rows={4}
          aria-label="Standard input piped to your program"
          placeholder={
            'Values your program reads with scanf()/std::cin.\n' +
            'One token per line or space separated, e.g.\n2 3'
          }
          className="w-full resize-y rounded-lg border border-slate-800 bg-[#050914] px-3 py-2 font-mono text-[12.5px] leading-relaxed text-slate-200 placeholder:text-slate-600 focus:border-sky-500/70 focus:outline-none focus:ring-1 focus:ring-sky-500/40"
        />
        <p className="font-mono text-[10px] text-slate-600">
          {stdin.length.toLocaleString()} char{stdin.length === 1 ? '' : 's'} fed to the sandbox
        </p>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* execution result                                                 */}
      {/* ---------------------------------------------------------------- */}
      <section
        className={`flex min-h-0 flex-1 flex-col gap-3 rounded-xl border bg-slate-900/40 p-3 ${
          statusStyle ? statusStyle.accent : 'border-slate-800'
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Terminal className="h-4 w-4 text-slate-400" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-200">Execution Result</h2>

          {running && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-sky-300">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Running
            </span>
          )}

          {!running && statusStyle && status && (
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-wide ${statusStyle.pill}`}
              title={statusStyle.hint}
            >
              <statusStyle.Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {statusStyle.label}
            </span>
          )}

          {!running && !statusStyle && !transportError && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
              Not run yet
            </span>
          )}
        </div>

        {/* stats */}
        <div className="flex gap-2">
          <Stat Icon={Cpu} label="Execution" value={response ? formatMs(response.execution_time_ms) : '-'} />
          <Stat Icon={Gauge} label="Compile" value={response ? formatMs(response.compile_time_ms) : '-'} />
          <Stat
            Icon={CodeXml}
            label="Exit code"
            value={exitLabel}
            tone={
              !response
                ? 'text-slate-100'
                : response.signal_name || (response.exit_code ?? 0) !== 0
                  ? 'text-amber-300'
                  : 'text-emerald-300'
            }
          />
        </div>

        {/* secondary flags */}
        {response && (response.truncated || response.timed_out || response.signal_name) && (
          <div className="flex flex-wrap gap-2 text-[11px]">
            {response.timed_out && (
              <span className="rounded-md border border-orange-500/40 bg-orange-500/10 px-2 py-0.5 text-orange-300">
                wall-clock limit hit
              </span>
            )}
            {response.signal_name && (
              <span className="rounded-md border border-yellow-500/40 bg-yellow-500/10 px-2 py-0.5 text-yellow-200">
                terminated by {response.signal_name}
              </span>
            )}
            {response.truncated && (
              <span className="rounded-md border border-slate-600 bg-slate-800/60 px-2 py-0.5 text-slate-300">
                output truncated by the judge
              </span>
            )}
          </div>
        )}

        {transportError && (
          <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            {transportError}
          </p>
        )}

        {statusStyle && status !== 'success' && (
          <p className="text-[11px] text-slate-400">{statusStyle.hint}</p>
        )}

        {/* tabs */}
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div role="tablist" aria-label="Judge output" className="flex items-center gap-1">
            {buildTabs(stdout, diagnostics).map((entry) => {
              const active = tab === entry.id
              return (
                <button
                  key={entry.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => {
                    // A tab choice is remembered only for the current run.
                    if (response) setChoice({ run: response, tab: entry.id })
                    else setChoice(null)
                  }}
                  className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? 'border-slate-700 bg-slate-800 text-slate-100'
                      : 'border-transparent text-slate-500 hover:bg-slate-800/50 hover:text-slate-300'
                  }`}
                >
                  {entry.label}
                  <span
                    className={`rounded px-1.5 font-mono text-[10px] ${
                      active ? 'bg-slate-900 text-slate-400' : 'bg-slate-800/70 text-slate-500'
                    }`}
                  >
                    {entry.badge}
                  </span>
                </button>
              )
            })}
          </div>

          {tab === 'stdout' ? (
            <TerminalBox
              text={stdout}
              prompt="$ solution > stdout"
              tone="stdout"
              emptyHint={
                response
                  ? '(the program wrote nothing to stdout)'
                  : '(press Ctrl+Enter to compile and run your code)'
              }
            />
          ) : (
            <TerminalBox
              text={diagnostics}
              prompt="$ compiler + stderr"
              tone="stderr"
              emptyHint="(no compiler warnings, errors or stderr output)"
            />
          )}
        </div>
      </section>
    </div>
  )
}

export default ConsolePanel
