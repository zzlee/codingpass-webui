/**
 * Phase 4 - submission review drawer (as a modal dialog).
 *
 * One row of the gradebook opened in detail:
 *  1. the submitted C/C++ source (`GET /api/submissions/{id}` - the backend
 *     only reveals it to the author, the problem's teacher and admins),
 *  2. the **AI 助教講評 / AI Code Review** section, which can (re)generate the
 *     review through `POST /api/submissions/{id}/ai-review` and renders the
 *     persisted Markdown feedback afterwards, and
 *  3. the **教師批改覆核 / Teacher Grade Review** section: staff get a score
 *     input (with quick-fill from the judge score or the AI suggestion) plus a
 *     feedback box saved via `PATCH /api/submissions/{id}/review`; students see
 *     the teacher's verdict read-only.
 *
 * Every save bubbles the fresh detail up via {@link onReviewed} so the list
 * badges (Teacher: X% / AI: X%) refresh without a manual reload.
 *
 * The parent mounts this with `key={submissionId}`, so switching attempts is a
 * fresh mount and the fetch effect only has to fill the empty state in.
 */
import { useCallback, useEffect, useState } from 'react'
import { CodeXml, Gavel, Save, Sparkles } from 'lucide-react'

import Markdown from './Markdown'
import {
  CopyButton,
  ErrorNotice,
  Field,
  LoadingRow,
  Modal,
  Spinner,
  VerdictBadge,
} from './ui'
import api from '../api'
import { describeError } from '../useAsync'
import {
  INPUT_CLASS,
  PRE_CLASS,
  buttonClass,
  countText,
  formatMs,
  formatRelative,
  formatScore,
  formatTimestamp,
  languageLabel,
} from '../format'
import type { AIAnalyzeResponse, SubmissionDetail } from '../types'

export interface SubmissionReviewModalProps {
  /** The attempt to open; the parent remounts this component per id. */
  submissionId: number
  /** True for `teacher` / `admin` accounts (they may grade). */
  staff: boolean
  onClose: () => void
  /** Called with the refreshed row after an AI or teacher review is stored. */
  onReviewed: (detail: SubmissionDetail) => void
}

export function SubmissionReviewModal({
  submissionId,
  staff,
  onClose,
  onReviewed,
}: SubmissionReviewModalProps) {
  const [detail, setDetail] = useState<SubmissionDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  // AI review state.
  const [analysis, setAnalysis] = useState<AIAnalyzeResponse | null>(null)
  const [busyAi, setBusyAi] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  // Teacher grading form state.
  const [scoreText, setScoreText] = useState('')
  const [feedbackText, setFeedbackText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedNote, setSavedNote] = useState<string | null>(null)

  /** Sync the grading form from a freshly loaded/saved detail. */
  const syncForm = useCallback((row: SubmissionDetail) => {
    setScoreText(row.teacher_score === null || row.teacher_score === undefined ? '' : String(row.teacher_score))
    setFeedbackText(row.teacher_feedback ?? '')
  }, [])

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const row = await api.getSubmission(submissionId)
      setDetail(row)
      syncForm(row)
    } catch (reason) {
      setLoadError(describeError(reason))
    }
  }, [submissionId, syncForm])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const row = await api.getSubmission(submissionId)
        if (cancelled) return
        setDetail(row)
        syncForm(row)
      } catch (reason) {
        if (!cancelled) setLoadError(describeError(reason))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [submissionId, syncForm])

  /* ------------------------------------------------------------ AI review */
  const generateAiReview = async () => {
    if (!detail || busyAi) return
    setBusyAi(true)
    setAiError(null)
    try {
      const result = await api.aiReviewSubmission(detail.id)
      setDetail(result)
      setAnalysis(result.analysis)
      onReviewed(result)
    } catch (reason) {
      setAiError(describeError(reason))
    } finally {
      setBusyAi(false)
    }
  }

  /* ------------------------------------------------------- teacher review */
  const saveReview = async () => {
    if (!detail || saving) return
    let score: number | null = null
    const raw = scoreText.trim()
    if (raw !== '') {
      const parsed = Number(raw)
      if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
        setSaveError('教師分數必須是 0 到 100 之間的數字 / Teacher score must be a number between 0 and 100.')
        return
      }
      score = parsed
    }
    setSaving(true)
    setSaveError(null)
    setSavedNote(null)
    try {
      const updated = await api.teacherReviewSubmission(detail.id, {
        teacher_score: score,
        teacher_feedback: feedbackText.trim() || null,
      })
      setDetail(updated)
      syncForm(updated)
      setSavedNote('已儲存批改 / Review saved.')
      onReviewed(updated)
    } catch (reason) {
      setSaveError(describeError(reason))
    } finally {
      setSaving(false)
    }
  }

  const aiMarkdown = analysis ? analysis.feedback_markdown : detail?.ai_feedback ?? ''
  const aiScore = analysis ? analysis.suggested_score : detail?.ai_suggested_score ?? null
  const aiModel = analysis
    ? analysis.ai_powered
      ? analysis.model
      : 'heuristic-rules'
    : detail?.ai_model ?? null
  const teacherReviewed =
    detail !== null && (detail.teacher_score !== null || (detail.teacher_feedback ?? '') !== '')

  return (
    <Modal
      open
      onClose={onClose}
      wide
      icon={<Gavel className="h-4 w-4" aria-hidden="true" />}
      title={detail ? `Review · ${detail.problem_title || `Problem #${detail.problem_id}`}` : `Submission #${submissionId}`}
      subtitle={
        detail
          ? `#${detail.id} · ${detail.student_name || detail.student_username} (${detail.student_username}) · ${languageLabel(detail.language)}`
          : 'Loading the attempt…'
      }
      footer={
        <>
          <button type="button" onClick={onClose} className={buttonClass('ghost', 'text-xs')}>
            Close
          </button>
          {staff ? (
            <button
              type="button"
              onClick={() => void saveReview()}
              disabled={saving || detail === null}
              title="Store the teacher score and feedback (PATCH /api/submissions/{id}/review)"
              className={buttonClass('primary', 'text-xs')}
            >
              {saving ? <Spinner className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
              <span>{saving ? 'Saving…' : 'Save Review'}</span>
            </button>
          ) : null}
        </>
      }
    >
      {loadError ? (
        <ErrorNotice message={loadError} onRetry={() => void load()} />
      ) : null}

      {!detail && !loadError ? <LoadingRow label="Loading submission…" /> : null}

      {detail ? (
        <div className="space-y-4">
          {/* Judge summary ----------------------------------------------- */}
          <div className="flex flex-wrap items-center gap-2">
            <VerdictBadge status={detail.status} />
            <span className="rounded-md border border-slate-700 bg-slate-900/70 px-2 py-1 font-mono text-[11px] text-slate-300">
              judge {formatScore(detail.score)} · {countText(detail.passed_count, detail.total_count)}
            </span>
            {detail.rubric_total !== undefined && detail.rubric_total > 0 ? (
              <span className="rounded-md border border-indigo-500/40 bg-indigo-500/10 px-2 py-1 font-mono text-[11px] font-semibold text-indigo-300">
                Rubric {detail.rubric_total} / 100
              </span>
            ) : null}
            {detail.presentation_warning ? (
              <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 font-mono text-[11px] text-amber-300">
                ⚠️ PE Warning
              </span>
            ) : null}
            <span className="rounded-md border border-slate-700 bg-slate-900/70 px-2 py-1 font-mono text-[11px] text-slate-400">
              {formatMs(detail.execution_time_ms)}
            </span>
            <span className="ml-auto font-mono text-[10px] text-slate-500">
              submitted {formatTimestamp(detail.created_at)}
            </span>
          </div>

          {detail.rubric ? (
            <div className="grid grid-cols-5 gap-2 text-center text-[10px] p-2 bg-slate-950/60 rounded-xl border border-slate-800">
              <div>
                <span className="text-slate-400">Compile</span>
                <div className="font-mono font-bold text-slate-200">{detail.rubric.compile_score} / 10</div>
              </div>
              <div>
                <span className="text-slate-400">Sample</span>
                <div className="font-mono font-bold text-slate-200">{detail.rubric.sample_score} / 10</div>
              </div>
              <div>
                <span className="text-slate-400">Random</span>
                <div className="font-mono font-bold text-slate-200">{detail.rubric.random_score} / 30</div>
              </div>
              <div>
                <span className="text-slate-400">Logic</span>
                <div className="font-mono font-bold text-indigo-300">{detail.rubric.logic_score} / 40</div>
              </div>
              <div>
                <span className="text-slate-400">Quality</span>
                <div className="font-mono font-bold text-slate-200">{detail.rubric.quality_score} / 10</div>
              </div>
            </div>
          ) : null}

          {detail.wording_violations && detail.wording_violations.length > 0 ? (
            <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs space-y-0.5">
              <span className="font-semibold text-rose-200">限碼規範違反項目：</span>
              <ul className="list-disc pl-4 font-mono text-[11px]">
                {detail.wording_violations.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Source code -------------------------------------------------- */}
          <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
            <div className="flex items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-2">
              <CodeXml className="h-4 w-4 text-sky-400" aria-hidden="true" />
              <span className="font-mono text-xs text-slate-300">
                solution.{detail.language === 'cpp' ? 'cpp' : 'c'}
              </span>
              <CopyButton value={detail.code} label="copy code" className="ml-auto" />
            </div>
            <pre className={`${PRE_CLASS} m-2 max-h-72`}>
              {detail.code.length ? detail.code : <span className="italic text-slate-600">(empty)</span>}
            </pre>
          </section>

          {/* AI code review ----------------------------------------------- */}
          <section className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Sparkles className="h-4 w-4 text-violet-400" aria-hidden="true" />
              <h3 className="text-xs font-semibold text-violet-200">
                AI 助教講評 <span className="font-normal text-violet-400/80">· AI Code Review</span>
              </h3>
              {aiScore !== null ? (
                <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-sky-300">
                  AI: {formatScore(aiScore)}
                </span>
              ) : null}
              {aiModel ? (
                <span className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[10px] text-violet-300">
                  {aiModel}
                </span>
              ) : null}
              {detail.ai_reviewed_at ? (
                <span className="font-mono text-[10px] text-slate-500">
                  {formatRelative(detail.ai_reviewed_at)}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => void generateAiReview()}
                disabled={busyAi}
                title="Run the AI tutor on this submission and store the review"
                className={buttonClass('accent', 'ml-auto text-[11px]')}
              >
                {busyAi ? <Spinner className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
                <span>{busyAi ? '產生中… Judging…' : '🤖 產生 AI 講評 (Generate AI Review)'}</span>
              </button>
            </div>

            {aiError ? (
              <div className="mb-2">
                <ErrorNotice message={aiError} />
              </div>
            ) : null}

            {analysis ? (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="rounded-md border border-slate-700/70 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  時間 {analysis.time_complexity || '未知'}
                </span>
                <span className="rounded-md border border-slate-700/70 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  空間 {analysis.space_complexity || '未知'}
                </span>
                {!analysis.ai_powered && analysis.detail ? (
                  <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                    規則引擎回覆：{analysis.detail}
                  </span>
                ) : null}
              </div>
            ) : null}

            {aiMarkdown ? (
              <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-1">
                <Markdown source={aiMarkdown} />
              </div>
            ) : busyAi ? (
              <div className="flex items-center gap-2 py-2 text-xs text-violet-300">
                <Spinner className="h-3.5 w-3.5" />
                AI 助教正在閱讀這份作答（可能需要數十秒）…
              </div>
            ) : (
              <p className="text-[11px] italic text-slate-500">
                尚未產生 AI 講評 / No AI review has been generated for this attempt yet.
              </p>
            )}
          </section>

          {/* Teacher grade review ------------------------------------------- */}
          <section className="rounded-xl border border-sky-500/30 bg-sky-950/20 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Gavel className="h-4 w-4 text-sky-400" aria-hidden="true" />
              <h3 className="text-xs font-semibold text-sky-200">
                教師批改覆核 <span className="font-normal text-sky-400/80">· Teacher Grade Review</span>
              </h3>
              {detail.reviewed_at ? (
                <span className="font-mono text-[10px] text-slate-500">
                  last graded {formatTimestamp(detail.reviewed_at)}
                </span>
              ) : null}
            </div>

            {staff ? (
              <div className="space-y-3">
                <Field label="教師分數 Teacher Score" hint="0-100，留空表示不覆核">
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={scoreText}
                      onChange={(event) => setScoreText(event.target.value)}
                      placeholder="e.g. 85"
                      className={`${INPUT_CLASS} max-w-[8rem] font-mono`}
                    />
                    <button
                      type="button"
                      onClick={() => setScoreText(String(detail.score))}
                      title="Use the automatic judge score"
                      className={buttonClass('ghost', 'px-2 py-1 text-[11px]')}
                    >
                      Judge {formatScore(detail.score)}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (detail.ai_suggested_score !== null && detail.ai_suggested_score !== undefined) {
                          setScoreText(String(detail.ai_suggested_score))
                        }
                      }}
                      disabled={detail.ai_suggested_score === null || detail.ai_suggested_score === undefined}
                      title="Use the AI tutor's suggested score"
                      className={buttonClass('ghost', 'px-2 py-1 text-[11px]')}
                    >
                      AI {formatScore(detail.ai_suggested_score)}
                    </button>
                  </div>
                </Field>

                <Field label="教師意見 Teacher Feedback" hint="選擇性，會顯示給學生">
                  <textarea
                    value={feedbackText}
                    onChange={(event) => setFeedbackText(event.target.value)}
                    rows={4}
                    placeholder="指出學生程式碼的具體問題與改進方向…"
                    className={`${INPUT_CLASS} resize-y font-mono text-xs`}
                  />
                </Field>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void saveReview()}
                    disabled={saving}
                    className={buttonClass('primary', 'text-xs')}
                  >
                    {saving ? <Spinner className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
                    <span>{saving ? 'Saving…' : 'Save Review'}</span>
                  </button>
                  {saveError ? <span className="text-[11px] text-rose-300">{saveError}</span> : null}
                  {savedNote ? <span className="text-[11px] text-emerald-300">{savedNote}</span> : null}
                </div>
              </div>
            ) : teacherReviewed ? (
              <div className="space-y-2 text-xs">
                <p className="flex items-center gap-2">
                  <span className="rounded border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 font-mono text-[11px] font-semibold text-sky-300">
                    Teacher: {formatScore(detail.teacher_score)}
                  </span>
                  {detail.reviewed_at ? (
                    <span className="font-mono text-[10px] text-slate-500">
                      {formatTimestamp(detail.reviewed_at)}
                    </span>
                  ) : null}
                </p>
                {detail.teacher_feedback ? (
                  <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-1">
                    <Markdown source={detail.teacher_feedback} />
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-[11px] italic text-slate-500">
                教師尚未人工批改此作答（成績來自自動裁判機）/ The teacher has not manually reviewed this
                submission yet.
              </p>
            )}
          </section>
        </div>
      ) : null}
    </Modal>
  )
}

export default SubmissionReviewModal
