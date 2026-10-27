/**
 * Top toolbar: branding, language switch, template picker, the Run CTA and the
 * live judge-health pill.
 */
import {
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CodeXml,
  LoaderCircle,
  Play,
  RefreshCw,
  SquareTerminal,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'

import type { CodeTemplate, TemplateId } from '../templates'
import { LANGUAGE_LABEL, SOURCE_EXTENSION, type BackendHealth, type Language } from '../types'

/** Small icon + label used by the language segmented control. */
const LANGUAGES: Array<{ value: Language; label: string; hint: string }> = [
  { value: 'c', label: 'C', hint: 'gcc -O2 -Wall' },
  { value: 'cpp', label: 'C++', hint: 'g++ -O2 -std=c++17' },
]

interface HealthPillStyle {
  className: string
  dotClassName: string
  Icon: LucideIcon
  label: string
}

const HEALTH_STYLES: Record<BackendHealth['status'], HealthPillStyle> = {
  checking: {
    className: 'border-slate-700 bg-slate-800/60 text-slate-300',
    dotClassName: 'bg-slate-400',
    Icon: CircleDashed,
    label: 'Checking',
  },
  online: {
    className: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    dotClassName: 'bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.55)]',
    Icon: CircleCheck,
    label: 'Judge online',
  },
  degraded: {
    className: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    dotClassName: 'bg-amber-400 shadow-[0_0_8px_2px_rgba(251,191,36,0.5)]',
    Icon: CircleAlert,
    label: 'Judge degraded',
  },
  offline: {
    className: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    dotClassName: 'bg-rose-500',
    Icon: WifiOff,
    label: 'Judge offline',
  },
}

export interface HeaderProps {
  language: Language
  onLanguageChange: (language: Language) => void
  templates: CodeTemplate[]
  /** Id of the template currently untouched in the editor (`''` = custom). */
  templateId: string
  onTemplateChange: (id: TemplateId) => void
  running: boolean
  onRun: () => void
  health: BackendHealth
  onHealthCheck: () => void
}

function HealthPill({ health, onHealthCheck }: { health: BackendHealth; onHealthCheck: () => void }) {
  const style = HEALTH_STYLES[health.status]
  const Icon = style.Icon
  return (
    <button
      type="button"
      onClick={onHealthCheck}
      title={`${health.detail}\nClick to re-check /api/health`}
      aria-label={`Backend status: ${style.label}. Re-check now.`}
      className={`group inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors hover:brightness-125 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${style.className}`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${style.dotClassName}`} aria-hidden="true" />
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="whitespace-nowrap">{style.label}</span>
      <RefreshCw
        className={`h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70 ${
          health.status === 'checking' ? 'animate-spin opacity-100' : ''
        }`}
        aria-hidden="true"
      />
    </button>
  )
}

export function Header({
  language,
  onLanguageChange,
  templates,
  templateId,
  onTemplateChange,
  running,
  onRun,
  health,
  onHealthCheck,
}: HeaderProps) {
  const sourceName = `solution.${SOURCE_EXTENSION[language]}`

  return (
    <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur supports-[backdrop-filter]:bg-slate-950/70">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3">
        {/* Branding ------------------------------------------------------- */}
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-sky-500 via-indigo-500 to-fuchsia-500 shadow-lg shadow-indigo-950/60">
            <SquareTerminal className="h-5 w-5 text-white" strokeWidth={2.2} aria-hidden="true" />
          </span>
          <div className="leading-tight">
            <div className="flex items-baseline gap-2">
              <span className="text-lg font-semibold tracking-tight text-white">CodingPass</span>
              <span className="hidden rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400 sm:inline">
                student
              </span>
            </div>
            <p className="text-[11px] text-slate-400">C / C++ online judge &amp; sandbox runner</p>
          </div>
        </div>

        {/* Language segmented control ------------------------------------- */}
        <div
          role="group"
          aria-label="Language"
          className="flex items-center gap-1 rounded-lg border border-slate-700/80 bg-slate-900/80 p-1"
        >
          {LANGUAGES.map((option) => {
            const active = option.value === language
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => onLanguageChange(option.value)}
                aria-pressed={active}
                title={`${LANGUAGE_LABEL[option.value]} - ${option.hint}`}
                className={`rounded-md px-3 py-1.5 text-sm font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
                  active
                    ? 'bg-sky-500/20 text-sky-200 ring-1 ring-inset ring-sky-400/50'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {option.label}
              </button>
            )
          })}
        </div>

        {/* Template picker ------------------------------------------------ */}
        <label className="relative flex items-center">
          <span className="sr-only">Load starter template</span>
          <CodeXml className="pointer-events-none absolute left-3 h-4 w-4 text-slate-500" aria-hidden="true" />
          <select
            value={templateId}
            onChange={(event) => {
              const next = event.target.value as TemplateId
              if (next) onTemplateChange(next)
            }}
            className="w-[15rem] max-w-[46vw] appearance-none rounded-lg border border-slate-700/80 bg-slate-900/80 py-2 pl-9 pr-8 text-sm text-slate-200 transition-colors hover:border-slate-600 focus:border-sky-500 focus:outline-none"
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
          <ChevronDown
            className="pointer-events-none absolute right-2.5 h-4 w-4 text-slate-500"
            aria-hidden="true"
          />
        </label>

        {/* Right hand side: run + health ---------------------------------- */}
        <div className="ml-auto flex items-center gap-3">
          <span
            className="hidden rounded-md border border-slate-700/70 bg-slate-900/70 px-2 py-1 font-mono text-[11px] text-slate-400 md:inline"
            title={`The judge compiles your code as ${sourceName}`}
          >
            {sourceName}
          </span>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onRun}
              disabled={running}
              title="Compile and run your submission (Ctrl+Enter / Cmd+Enter)"
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-emerald-950/40 transition-all hover:bg-emerald-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {running ? (
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Play className="h-4 w-4" fill="currentColor" aria-hidden="true" />
              )}
              <span>{running ? 'Compiling…' : 'Run Code'}</span>
            </button>

            <kbd
              className="hidden select-none items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[10px] text-slate-400 lg:inline-flex"
              title="Keyboard shortcut: Ctrl+Enter (Cmd+Enter on macOS)"
            >
              Ctrl
              <span className="text-slate-600">+</span>
              Enter
            </kbd>
          </div>

          <HealthPill health={health} onHealthCheck={onHealthCheck} />
        </div>
      </div>
    </header>
  )
}

export default Header
