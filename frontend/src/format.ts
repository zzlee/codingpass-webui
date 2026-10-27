/**
 * Small presentation helpers shared by every panel.
 *
 * Kept out of the component modules so `.tsx` files only export components
 * (which keeps the `react/only-export-components` lint rule quiet) and so the
 * formatting rules are unit-testable in isolation.
 */
import type { Language, Verdict } from './types'

/** Milliseconds tile formatter (shared with the Phase 1 console panel). */
export { formatMs } from './types'

/** Compact verdict labels used in badges and table cells. */
export const VERDICT_LABEL: Record<Verdict, string> = {
  accepted: 'ACCEPTED',
  wrong_answer: 'WRONG ANSWER',
  time_limit_exceeded: 'TIME LIMIT EXCEEDED',
  compile_error: 'COMPILE ERROR',
  runtime_error: 'RUNTIME ERROR',
}

/** Emoji shown next to the verdict (requested by the grading panel spec). */
export const VERDICT_EMOJI: Record<Verdict, string> = {
  accepted: '🎉',
  wrong_answer: '❌',
  time_limit_exceeded: '⏱️',
  compile_error: '🛑',
  runtime_error: '💥',
}

/** Tailwind classes per verdict: pill, accent border and raw text colour. */
export const VERDICT_TONE: Record<Verdict, { pill: string; border: string; text: string; bar: string }> =
  {
    accepted: {
      pill: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
      border: 'border-emerald-500/40',
      text: 'text-emerald-300',
      bar: 'bg-emerald-500',
    },
    wrong_answer: {
      pill: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
      border: 'border-rose-500/40',
      text: 'text-rose-300',
      bar: 'bg-rose-500',
    },
    time_limit_exceeded: {
      pill: 'border-orange-500/40 bg-orange-500/15 text-orange-300',
      border: 'border-orange-500/40',
      text: 'text-orange-300',
      bar: 'bg-orange-500',
    },
    compile_error: {
      pill: 'border-fuchsia-500/40 bg-fuchsia-500/15 text-fuchsia-300',
      border: 'border-fuchsia-500/40',
      text: 'text-fuchsia-300',
      bar: 'bg-fuchsia-500',
    },
    runtime_error: {
      pill: 'border-yellow-500/40 bg-yellow-500/15 text-yellow-200',
      border: 'border-yellow-500/40',
      text: 'text-yellow-200',
      bar: 'bg-yellow-500',
    },
  }

/** Difficulty -> label and Tailwind classes (green / yellow / red). */
export const DIFFICULTY_TONE: Record<
  string,
  { label: string; pill: string; dot: string }
> = {
  easy: {
    label: 'EASY',
    pill: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    dot: 'bg-emerald-400',
  },
  medium: {
    label: 'MEDIUM',
    pill: 'border-yellow-500/40 bg-yellow-500/10 text-yellow-200',
    dot: 'bg-yellow-400',
  },
  hard: {
    label: 'HARD',
    pill: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    dot: 'bg-rose-400',
  },
}

/** Role chip colours. */
export const ROLE_TONE: Record<string, string> = {
  admin: 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-200',
  teacher: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
  student: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
}

/** `1234` -> `'1,234'`; anything odd falls back to the raw string. */
export function groupThousands(value: number | string): string {
  const digits = String(value)
  if (!/^-?\d+$/.test(digits)) return digits
  const [sign, body = digits] = digits.startsWith('-') ? ['-', digits.slice(1)] : ['', digits]
  return sign + body.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** Score (0-100 float) rendered as `87.5%` without a trailing `.0`. */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return '-'
  const rounded = Math.round(score * 10) / 10
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}%`
}

/** `4.2` -> `'4.2s'`, `3` -> `'3s'`. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '-'
  return `${Number.isInteger(seconds) ? seconds.toFixed(0) : seconds.toFixed(1)}s`
}

/** ISO-8601 UTC stamp -> short local date/time for tables. */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** ISO-8601 UTC stamp -> `'3 min ago'` style hint. */
export function formatRelative(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const deltaSec = Math.round((Date.now() - date.getTime()) / 1000)
  if (deltaSec < 45) return 'just now'
  if (deltaSec < 3600) return `${Math.round(deltaSec / 60)} min ago`
  if (deltaSec < 86_400) return `${Math.round(deltaSec / 3600)} h ago`
  if (deltaSec < 86_400 * 30) return `${Math.round(deltaSec / 86_400)} d ago`
  return formatTimestamp(iso)
}

/** Display name for an account: full name when set, else the username. */
export function displayName(user: { full_name: string; username: string } | null): string {
  if (!user) return 'Guest'
  return user.full_name.trim() || user.username
}

/** Initials for the avatar chip (max 2 letters). */
export function initials(user: { full_name: string; username: string } | null): string {
  if (!user) return '?'
  const source = user.full_name.trim() || user.username
  const parts = source.split(/[\s_-]+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return source.slice(0, 2).toUpperCase()
}

/**
 * Mirror of the judge's `_normalize_output` (`backend/main.py`).
 *
 * Ignored: CRs, trailing spaces/tabs per line and leading/trailing blank lines.
 * Still significant: line order, internal spacing and capitalisation. Keeping
 * the browser-side comparison identical means "Run Samples" agrees with Submit.
 */
export function normalizeOutput(text: string | null | undefined): string {
  if (!text) return ''
  const lines = text.replace(/\r\n?/g, '\n').split('\n').map((line) => line.replace(/[ \t]+$/, ''))
  while (lines.length && lines[0] === '') lines.shift()
  while (lines.length && lines[lines.length - 1] === '') lines.pop()
  return lines.join('\n')
}

/** `true` when two outputs match under the judge's rules. */
export function outputsMatch(actual: string | null | undefined, expected: string | null | undefined): boolean {
  return normalizeOutput(actual) === normalizeOutput(expected)
}

/** Shared input class string for text inputs / selects / textareas. */
export const INPUT_CLASS =
  'w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 ' +
  'placeholder:text-slate-500 transition-colors hover:border-slate-600 focus:border-sky-500 ' +
  'focus:outline-none focus:ring-1 focus:ring-sky-500/40'

/** Shared button class builder for the few button flavours the UI needs. */
export function buttonClass(
  variant: 'primary' | 'accent' | 'ghost' | 'danger' | 'subtle' = 'subtle',
  extra = '',
): string {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ' +
    'transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ' +
    'disabled:cursor-not-allowed disabled:opacity-60'
  const variants: Record<typeof variant, string> = {
    primary: 'bg-emerald-500 text-slate-950 shadow-lg shadow-emerald-950/40 hover:bg-emerald-400',
    accent: 'bg-sky-500 text-slate-950 shadow-lg shadow-sky-950/40 hover:bg-sky-400',
    danger: 'border border-rose-500/40 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20',
    ghost: 'border border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:text-slate-100',
    subtle: 'border border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:text-slate-100',
  }
  return `${base} ${variants[variant]} ${extra}`.trim()
}

/** Language label used in tables (`c` -> `C`). */
export function languageLabel(language: string): string {
  return language === 'cpp' ? 'C++' : language === 'c' ? 'C' : language
}

/** Small helper for the `x / y` counters. */
export function countText(passed: number, total: number): string {
  return `${passed} / ${total}`
}

/** Mono class for `<pre>` blocks that show program output. */
export const PRE_CLASS =
  'max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-800 ' +
  'bg-slate-950/70 p-2 font-mono text-[11px] leading-relaxed text-slate-300'

/** Guard used when a language key arrives from the wire. */
export function asLanguage(value: string): Language {
  return value === 'cpp' ? 'cpp' : 'c'
}
