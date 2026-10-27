/**
 * Shared presentational primitives: modal dialog, badges, form fields and the
 * empty / error placeholders every panel reuses.
 *
 * Only components are exported here (helpers live in `../format.ts`) so the
 * `react/only-export-components` lint rule stays quiet.
 */
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  Check,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  ClipboardCopy,
  Inbox,
  LoaderCircle,
  TimerReset,
  TriangleAlert,
  X,
  type LucideIcon,
} from 'lucide-react'

import {
  DIFFICULTY_TONE,
  ROLE_TONE,
  VERDICT_EMOJI,
  VERDICT_LABEL,
  VERDICT_TONE,
  buttonClass,
} from '../format'
import type { Difficulty, Role, Verdict } from '../types'

/* -------------------------------------------------------------------------- */
/* Modal                                                                       */
/* -------------------------------------------------------------------------- */

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  icon?: ReactNode
  children: ReactNode
  footer?: ReactNode
  /** `wide` gives the create-problem form room for its test case editors. */
  wide?: boolean
}

/**
 * Accessible modal: `Esc` closes it, the backdrop closes it, focus lands on the
 * dialog and body scrolling is locked while it is open.
 */
export function Modal({ open, onClose, title, subtitle, icon, children, footer, wide }: ModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panelRef.current?.focus()
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/80 p-4 backdrop-blur-sm sm:items-center"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`my-auto w-full rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl shadow-slate-950/80 outline-none ${
          wide ? 'max-w-4xl' : 'max-w-lg'
        }`}
      >
        <div className="flex items-start gap-3 border-b border-slate-800 px-5 py-4">
          {icon ? <span className="mt-0.5 text-sky-400">{icon}</span> : null}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-white">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>

        {footer ? (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-800 px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Badges                                                                      */
/* -------------------------------------------------------------------------- */

const VERDICT_ICONS: Record<Verdict, LucideIcon> = {
  accepted: CircleCheck,
  wrong_answer: CircleAlert,
  time_limit_exceeded: TimerReset,
  compile_error: TriangleAlert,
  runtime_error: CircleSlash,
}

/** Verdict chip: ACCEPTED 🎉 green, WRONG ANSWER ❌ red, TLE ⏱️ orange, … */
export function VerdictBadge({
  status,
  withEmoji = true,
  size = 'sm',
}: {
  status: Verdict
  withEmoji?: boolean
  size?: 'sm' | 'lg'
}) {
  const tone = VERDICT_TONE[status] ?? VERDICT_TONE.runtime_error
  const Icon = VERDICT_ICONS[status] ?? CircleAlert
  const big = size === 'lg'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-mono font-semibold uppercase tracking-wide ${tone.pill} ${
        big ? 'px-3 py-1.5 text-sm' : 'px-2 py-0.5 text-[10px]'
      }`}
    >
      <Icon className={big ? 'h-4 w-4' : 'h-3 w-3'} aria-hidden="true" />
      {withEmoji ? <span aria-hidden="true">{VERDICT_EMOJI[status]}</span> : null}
      {VERDICT_LABEL[status] ?? status}
    </span>
  )
}

/** Difficulty chip: Easy green, Medium yellow, Hard red. */
export function DifficultyBadge({ difficulty }: { difficulty: Difficulty | string }) {
  const tone = DIFFICULTY_TONE[difficulty] ?? DIFFICULTY_TONE.medium
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${tone.pill}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden="true" />
      {tone.label}
    </span>
  )
}

/** Role chip. */
export function RoleBadge({ role }: { role: Role | string }) {
  return (
    <span
      className={`rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
        ROLE_TONE[role] ?? 'border-slate-700 bg-slate-800 text-slate-300'
      }`}
    >
      {role}
    </span>
  )
}

/** Neutral metadata chip. */
export function MetaChip({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-700/70 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
      {icon}
      {children}
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Feedback                                                                    */
/* -------------------------------------------------------------------------- */

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return <LoaderCircle className={`${className} animate-spin`} aria-hidden="true" />
}

/** Inline loading row for panels. */
export function LoadingRow({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Spinner />
      {label}
    </div>
  )
}

/** Red banner for API failures. */
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
    >
      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{message}</span>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-rose-400/40 px-2 py-0.5 text-[11px] font-semibold text-rose-100 transition-colors hover:bg-rose-500/20"
        >
          Retry
        </button>
      ) : null}
    </div>
  )
}

/** Blue informational banner. */
export function InfoNotice({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-200">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  )
}

/** Placeholder shown when a list has nothing in it. */
export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-slate-800 bg-slate-900/30 px-6 py-12 text-center">
      <span className="text-slate-600">{icon ?? <Inbox className="h-8 w-8" aria-hidden="true" />}</span>
      <div>
        <p className="text-sm font-semibold text-slate-300">{title}</p>
        {hint ? <p className="mt-1 max-w-md text-xs text-slate-500">{hint}</p> : null}
      </div>
      {action ? <div className="mt-1 flex justify-center">{action}</div> : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Form                                                                        */
/* -------------------------------------------------------------------------- */

/** Labelled form row with optional hint and error line. */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  className = '',
  children,
}: {
  label: string
  hint?: string
  error?: string
  htmlFor?: string
  className?: string
  children: ReactNode
}) {
  return (
    <label htmlFor={htmlFor} className={`block ${className}`}>
      <span className="mb-1 flex items-baseline gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</span>
        {hint ? <span className="text-[11px] font-normal text-slate-500">{hint}</span> : null}
      </span>
      {children}
      {error ? <span className="mt-1 block text-[11px] text-rose-300">{error}</span> : null}
    </label>
  )
}

/** Two-state segmented control (language pickers, filters). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = 'md',
}: {
  options: Array<{ value: T; label: string; hint?: string }>
  value: T
  onChange: (value: T) => void
  ariaLabel: string
  size?: 'sm' | 'md'
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-1 rounded-lg border border-slate-700/80 bg-slate-900/80 p-1"
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            title={option.hint}
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`rounded-md font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
              size === 'sm' ? 'px-2 py-1 text-[11px]' : 'px-3 py-1.5 text-sm'
            } ${
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
  )
}

/** Copy-to-clipboard icon button (used for invite codes and outputs). */
export function CopyButton({
  value,
  label = 'Copy',
  className = '',
}: {
  value: string
  label?: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    [],
  )

  return (
    <button
      type="button"
      title={`${label} to clipboard`}
      onClick={() => {
        const flash = () => {
          setCopied(true)
          if (timerRef.current !== null) window.clearTimeout(timerRef.current)
          timerRef.current = window.setTimeout(() => setCopied(false), 1400)
        }
        if (!navigator.clipboard) {
          flash()
          return
        }
        void navigator.clipboard.writeText(value).then(
          flash,
          () => setCopied(false),
        )
      }}
      className={buttonClass('ghost', `px-2 py-1 text-[11px] ${className}`)}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
      ) : (
        <ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      <span>{copied ? 'Copied' : label}</span>
    </button>
  )
}
