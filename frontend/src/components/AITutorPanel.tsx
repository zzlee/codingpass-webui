/**
 * Phase 4 - AI 助教診斷面板 / AI Tutor diagnostics.
 *
 * A single self-contained panel that calls `POST /api/ai/analyze` with the
 * *current editor buffer* and whatever judge diagnostics are known (compile
 * output, stderr, verdict). Nothing is persisted on the server: this is the
 * student's scratchpad reviewer, usable from the Playground and the Solver.
 *
 * The backend degrades gracefully - when the vLLM model is unreachable it
 * answers with the heuristic reviewer and `ai_powered === false`, which the
 * model badge surfaces to the student ("heuristic-rules" instead of the real
 * model id). Re-pressing the button always re-analyses the current code.
 */
import { useCallback, useRef, useState } from 'react'
import {
  CircleAlert,
  Gauge,
  Lightbulb,
  Sparkles,
  Timer,
  WandSparkles,
} from 'lucide-react'

import api from '../api'
import { describeError } from '../useAsync'
import { buttonClass, formatScore } from '../format'
import { Spinner } from './ui'
import type { AIAnalyzeResponse, Language } from '../types'

export interface AITutorPanelProps {
  /** Source that will be sent for analysis (the live editor buffer). */
  code: string
  language: Language
  /** Judge verdict for this source, if a run happened (e.g. `compile_error`). */
  status?: string
  /** Compiler diagnostics from the last run, if any. */
  compileOutput?: string
  /** Program stderr from the last run, if any. */
  stderr?: string
  /** Attach the problem statement as context (requires authentication). */
  problemId?: number | null
  /** Tighter paddings for the sidebar placements. */
  compact?: boolean
}

export function AITutorPanel({
  code,
  language,
  status,
  compileOutput,
  stderr,
  problemId,
  compact = false,
}: AITutorPanelProps) {
  const [analysis, setAnalysis] = useState<AIAnalyzeResponse | null>(null)
  const [expanded, setExpanded] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Guards against double-presses while a (up to 45 s) request is in flight.
  const busyRef = useRef(false)

  const analyze = useCallback(async () => {
    if (busyRef.current) return
    if (!code.trim()) {
      setError('There is no code to analyse yet - write a solution first.')
      setAnalysis(null)
      return
    }
    busyRef.current = true
    setLoading(true)
    setError(null)
    setAnalysis(null)
    setExpanded(true)
    try {
      const result = await api.aiAnalyze({
        code,
        language,
        status: status ?? 'unknown',
        compile_output: compileOutput ?? '',
        stderr: stderr ?? '',
        problem_id: problemId ?? null,
      })
      setAnalysis(result)
    } catch (reason) {
      setError(describeError(reason))
    } finally {
      setLoading(false)
      busyRef.current = false
    }
  }, [code, language, status, compileOutput, stderr, problemId])

  const pad = compact ? 'px-2.5 py-1.5' : 'px-3 py-2'
  const modelName = analysis ? (analysis.ai_powered ? analysis.model : 'heuristic-rules') : null

  return (
    <div className="shrink-0 overflow-hidden rounded-xl border border-violet-500/30 bg-violet-950/20">
      {/* Header ----------------------------------------------------------- */}
      <div className={`flex flex-wrap items-center gap-2 ${pad}`}>
        <Sparkles className="h-4 w-4 shrink-0 text-violet-400" aria-hidden="true" />
        <span className="text-xs font-semibold text-violet-200">
          AI 助教診斷 <span className="font-normal text-violet-400/80">· AI Tutor</span>
        </span>

        {analysis && modelName ? (
          <span className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[10px] text-violet-300">
            {modelName}
          </span>
        ) : null}
        {analysis ? (
          <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-sky-300">
            AI 建議分數 {formatScore(analysis.suggested_score)}
          </span>
        ) : null}

        <div className="ml-auto flex items-center gap-1.5">
          {analysis ? (
            <button
              type="button"
              onClick={() => setExpanded((previous) => !previous)}
              aria-expanded={expanded}
              title={expanded ? 'Collapse the AI review' : 'Expand the AI review'}
              className={buttonClass('ghost', 'px-2 py-1 text-[11px]')}
            >
              {expanded ? '收起' : '展開'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void analyze()}
            disabled={loading}
            title="Send the current source to the AI tutor for diagnostics"
            className={buttonClass('accent', `text-[11px] ${loading ? 'opacity-80' : ''}`)}
          >
            {loading ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <WandSparkles className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{loading ? '診斷中…' : analysis ? '重新診斷 / Re-analyze' : 'AI 助教診斷 / Ask AI Tutor'}</span>
          </button>
        </div>
      </div>

      {/* Body -------------------------------------------------------------- */}
      {loading ? (
        <div className={`flex items-center gap-2 border-t border-violet-500/20 ${pad} text-[11px] text-violet-300`}>
          <Spinner className="h-3.5 w-3.5" />
          AI 助教正在閱讀程式碼並撰寫講評（模型回應可能需要數十秒）…
        </div>
      ) : null}

      {error ? (
        <div className={`border-t border-rose-500/20 ${pad}`}>
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-[11px] text-rose-200"
          >
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 break-words">{error}</span>
          </div>
        </div>
      ) : null}

      {analysis && expanded ? (
        <div className={`space-y-2 border-t border-violet-500/20 ${pad} text-xs`}>
          {/* Complexity chips + heuristic notice -------------------------- */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="inline-flex items-center gap-1 rounded-md border border-slate-700/70 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              <Timer className="h-3 w-3 text-orange-400" aria-hidden="true" />
              時間複雜度 {analysis.time_complexity || '未知'}
            </span>
            <span className="inline-flex items-center gap-1 rounded-md border border-slate-700/70 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              <Gauge className="h-3 w-3 text-sky-400" aria-hidden="true" />
              空間複雜度 {analysis.space_complexity || '未知'}
            </span>
          </div>

          {!analysis.ai_powered && analysis.detail ? (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
              本次由內建規則回覆（AI 模型暫時不可用）：{analysis.detail}
            </p>
          ) : null}

          {analysis.summary ? (
            <p className="whitespace-pre-wrap break-words leading-relaxed text-slate-200">
              {analysis.summary}
            </p>
          ) : null}

          {analysis.error_analysis.trim() ? (
            <div>
              <p className="mb-0.5 font-mono text-[10px] uppercase tracking-wider text-rose-300">
                錯誤分析 · Error analysis
              </p>
              <p className="whitespace-pre-wrap break-words rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1.5 leading-relaxed text-slate-300">
                {analysis.error_analysis}
              </p>
            </div>
          ) : null}

          {analysis.suggestions.length > 0 ? (
            <div>
              <p className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-emerald-300">
                <Lightbulb className="h-3 w-3" aria-hidden="true" />
                改進建議 · Suggestions
              </p>
              <ul className="list-disc space-y-1 pl-5 leading-relaxed text-slate-300 marker:text-violet-400">
                {analysis.suggestions.map((suggestion, index) => (
                  <li key={index} className="break-words">
                    {suggestion}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="font-mono text-[10px] text-slate-600">
            AI 講評僅供參考，正式成績以裁判機與教師批改為準 · AI feedback is advisory
          </p>
        </div>
      ) : null}
    </div>
  )
}

export default AITutorPanel
