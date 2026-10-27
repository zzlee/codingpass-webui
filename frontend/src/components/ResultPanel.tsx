/**
 * Grading result panel.
 *
 * Shows the aggregate verdict (ACCEPTED 🎉 / WRONG ANSWER ❌ / TLE ⏱️ /
 * COMPILE ERROR / RUNTIME ERROR), the 0-100 score, passed/total, timings and a
 * per-test-case breakdown.
 *
 * Hidden cases stay redacted on purpose: `GET /api/problems/{id}/submit` never
 * leaks their stdin, expected output or the program's stdout to a student (see
 * `_case_verdict` in `backend/main.py`), so this panel can only show Pass/Fail
 * for them. Sample cases expand to show input / expected / got side by side.
 */
import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Clock,
  Cpu,
  Info,
  Lock,
  Percent,
  TriangleAlert,
} from 'lucide-react'

import { MetaChip, VerdictBadge } from './ui'
import { PRE_CLASS, VERDICT_TONE, countText, formatMs, formatScore } from '../format'
import type { CaseRowView, JudgeReportView } from '../judgeView'

function Row({ row, index }: { row: CaseRowView; index: number }) {
  const [open, setOpen] = useState(index === 0 && !row.passed)
  const tone = VERDICT_TONE[row.status] ?? VERDICT_TONE.runtime_error

  return (
    <li className={`overflow-hidden rounded-lg border bg-slate-950/40 ${row.passed ? 'border-slate-800' : tone.border}`}>
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-slate-800/40"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
        )}
        <span className="w-[5.5rem] shrink-0 font-mono text-[11px] text-slate-300">{row.label}</span>
        {row.passed ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-emerald-300">
            <CircleCheck className="h-3 w-3" aria-hidden="true" />
            PASS
          </span>
        ) : (
          <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${tone.pill}`}>
            {row.status.replace(/_/g, ' ')}
          </span>
        )}
        {row.redacted ? (
          <span className="inline-flex items-center gap-1 text-[10px] text-slate-500" title="Hidden test case - contents are never sent to students">
            <Lock className="h-3 w-3" aria-hidden="true" />
            hidden
          </span>
        ) : null}
        {row.truncated ? (
          <span className="text-[10px] text-amber-400" title="Output was truncated by the judge">
            truncated
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-2 font-mono text-[10px] text-slate-500">
          {row.exitCode !== null && !row.passed ? <span>exit {row.exitCode}</span> : null}
          {row.signalName ? <span>{row.signalName}</span> : null}
          <span>{formatMs(row.timeMs)}</span>
        </span>
      </button>

      {open ? (
        <div className="border-t border-slate-800/80 px-2.5 py-2">
          {row.redacted ? (
            <p className="flex items-start gap-2 text-[11px] text-slate-400">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              This is a hidden grading case. Its input and expected output are never exposed -
              only the verdict and the timing are returned.
            </p>
          ) : (
            <div className="grid gap-2 md:grid-cols-3">
              <div>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                  stdin
                </p>
                <pre className={PRE_CLASS}>{row.input && row.input.length ? row.input : <span className="italic text-slate-600">(empty)</span>}</pre>
              </div>
              <div>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                  expected
                </p>
                <pre className={PRE_CLASS}>{row.expected && row.expected.length ? row.expected : <span className="italic text-slate-600">(empty)</span>}</pre>
              </div>
              <div>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                  your output
                </p>
                <pre
                  className={`${PRE_CLASS} ${row.passed ? 'border-emerald-500/30' : 'border-rose-500/30'} ${
                    row.passed ? 'text-emerald-200' : 'text-rose-200'
                  }`}
                >
                  {row.got && row.got.length ? row.got : <span className="italic text-slate-600">(no output)</span>}
                </pre>
              </div>
            </div>
          )}

          {row.stderr ? (
            <div className="mt-2">
              <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                stderr
              </p>
              <pre className={`${PRE_CLASS} text-amber-200/90`}>{row.stderr}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

export interface ResultPanelProps {
  report: JudgeReportView
  /** Extra banner shown above the breakdown (e.g. a transport error). */
  notice?: string | null
}

export function ResultPanel({ report, notice }: ResultPanelProps) {
  const tone = VERDICT_TONE[report.verdict] ?? VERDICT_TONE.runtime_error
  const [showCompile, setShowCompile] = useState(false)
  const scorePercent = Math.max(0, Math.min(100, report.score))

  return (
    <section
      className={`flex min-h-0 flex-col overflow-hidden rounded-xl border bg-slate-900/50 ${tone.border}`}
      aria-label="Judge result"
    >
      {/* Verdict header ------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-800 px-3 py-2.5">
        <VerdictBadge status={report.verdict} size="lg" />

        <div className="flex items-baseline gap-1.5">
          <span className={`font-mono text-lg font-bold leading-none ${tone.text}`}>
            {formatScore(report.score)}
          </span>
          <span className="text-[11px] text-slate-500">score</span>
        </div>

        <div className="flex items-center gap-1.5">
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-800" role="presentation">
            <div
              className={`h-full rounded-full ${tone.bar}`}
              style={{ width: `${scorePercent}%` }}
            />
          </div>
          <span className="font-mono text-[11px] text-slate-400">
            {countText(report.passed, report.total)}
          </span>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <MetaChip icon={<Clock className="h-3 w-3" aria-hidden="true" />} >
            {formatMs(report.timeMs)}
          </MetaChip>
          {report.compileTimeMs > 0 ? (
            <MetaChip icon={<Cpu className="h-3 w-3" aria-hidden="true" />}>
              compile {formatMs(report.compileTimeMs)}
            </MetaChip>
          ) : null}
          <MetaChip>{report.language === 'cpp' ? 'C++' : 'C'}</MetaChip>
        </div>
      </div>

      {/* Summary strip --------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-slate-800 bg-slate-950/40 px-3 py-1.5 text-[11px]">
        <span className="font-semibold text-slate-300">{report.heading}</span>
        {report.hidden ? (
          <span className="inline-flex items-center gap-1.5 font-mono text-slate-400">
            <Lock className="h-3 w-3" aria-hidden="true" />
            hidden {countText(report.hidden.passed, report.hidden.total)}
            {report.hidden.failed > 0 ? (
              <span className="text-rose-300">({report.hidden.failed} failed)</span>
            ) : (
              <span className="text-emerald-300">all passed</span>
            )}
          </span>
        ) : null}
        {report.notRecorded ? (
          <span className="inline-flex items-center gap-1 text-sky-300">
            <Info className="h-3 w-3" aria-hidden="true" />
            not stored in the gradebook
          </span>
        ) : report.submissionId ? (
          <span className="font-mono text-slate-500">submission #{report.submissionId}</span>
        ) : null}
        {report.total > 0 && report.passed === report.total ? (
          <span className="inline-flex items-center gap-1 text-emerald-300">
            <Percent className="h-3 w-3" aria-hidden="true" />
            every case passed
          </span>
        ) : null}
      </div>

      {notice ? (
        <p className="flex items-start gap-2 border-b border-slate-800 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">{notice}</span>
        </p>
      ) : null}

      {/* Compile diagnostics --------------------------------------------- */}
      {report.compileOutput ? (
        <div className="border-b border-slate-800 px-3 py-2">
          <button
            type="button"
            onClick={() => setShowCompile((previous) => !previous)}
            className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-300"
          >
            {showCompile ? (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden="true" />
            )}
            compiler output
          </button>
          {showCompile ? (
            <pre className={`${PRE_CLASS} mt-1.5 max-h-40 text-amber-200/90`}>
              {report.compileOutput}
            </pre>
          ) : null}
        </div>
      ) : null}

      {/* Per case breakdown ---------------------------------------------- */}
      <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2.5">
        {report.rows.map((row, index) => (
          <Row key={row.key} row={row} index={index} />
        ))}
        {report.rows.length === 0 ? (
          <li className="py-6 text-center text-xs text-slate-500">
            No test cases were executed.
          </li>
        ) : null}
      </ul>
    </section>
  )
}

export default ResultPanel
