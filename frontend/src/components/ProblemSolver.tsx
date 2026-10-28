/**
 * Problem solving view: statement on the left, Monaco + judging on the right.
 *
 * Two judge paths live here:
 *  - **Run Samples** is a scratchpad - nothing is stored. When the CodingPass
 *    Local Agent is connected it sends one `run_batch` over the local WebSocket,
 *    which compiles once and runs every sample case on the student's own machine;
 *    otherwise (or when that request is refused or answers short) it falls back to
 *    `POST /api/run` once per sample case. Either way the output is compared
 *    locally with the exact rules the judge uses (`normalizeOutput`).
 *  - **Submit Solution** calls `POST /api/problems/{id}/submit`, which grades
 *    against every case including the hidden ones and records the attempt for
 *    students (a teacher/admin run is returned as `trial_run` and not stored).
 *
 * The editor buffers are kept in the shared workspace store, so hopping to the
 * Classrooms tab and back keeps unsaved work.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Editor, { type OnChange, type OnMount } from '@monaco-editor/react'
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Clock,
  EyeOff,
  FileText,
  Gauge,
  Layers,
  LoaderCircle,
  Lock,
  Play,
  RotateCcw,
  Send,
  Terminal,
  Trophy,
} from 'lucide-react'

import '../monacoSetup'
import { EDITOR_OPTIONS, EDITOR_THEME } from '../monacoSetup'
import AITutorPanel from './AITutorPanel'
import CustomTestPanel, { type CustomTestResult } from './CustomTestPanel'
import ExamLockdownOverlay from './ExamLockdownOverlay'
import Markdown from './Markdown'
import ResultPanel from './ResultPanel'
import { CopyButton, DifficultyBadge, EmptyState, ErrorNotice, MetaChip, Segmented, Spinner } from './ui'
import { useAuth } from '../authContext'
import { useCtrlEnter } from '../shortcuts'
import { useWorkspace } from '../workspaceContext'
import { useLocalAgent } from '../localAgentContext'
import api, { ApiError } from '../api'
import {
  fromSampleRuns,
  fromSubmissionResult,
  type JudgeReportView,
  type SampleRun,
} from '../judgeView'
import {
  PRE_CLASS,
  VERDICT_TONE,
  buttonClass,
  countText,
  formatScore,
  outputsMatch,
} from '../format'
import {
  LANGUAGE_LABEL,
  MONACO_LANGUAGE,
  SOURCE_EXTENSION,
  type Language,
  type ProblemDetail,
  type TestCasePublic,
} from '../types'

const LANGUAGE_OPTIONS: Array<{ value: Language; label: string; hint: string }> = [
  { value: 'c', label: 'C', hint: 'gcc -O2 -Wall' },
  { value: 'cpp', label: 'C++', hint: 'g++ -O2 -std=c++17' },
]

/** Result-panel heading for runs that never touch the server. */
const LOCAL_RUN_HEADING = '本地加速執行 (Local Agent) - 未計入成績'

/**
 * Client side deadline for a whole submission: every case may burn the problem's
 * time limit plus a compile allowance, so a 10-case problem at 2s needs ~35s.
 */
function submissionTimeout(problem: ProblemDetail): number {
  const cases = Math.max(1, problem.sample_count + problem.hidden_count)
  return Math.min(600_000, Math.max(60_000, cases * (problem.time_limit_sec + 2) * 1000 + 15_000))
}

export interface ProblemSolverProps {
  problemId: number
  onBack: () => void
}

export function ProblemSolver({ problemId, onBack }: ProblemSolverProps) {
  const { user } = useAuth()
  const { getDraft, setDraft } = useWorkspace()
  const localAgent = useLocalAgent()

  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [language, setLanguage] = useState<Language>('c')
  const [sources, setSources] = useState<Record<Language, string>>({ c: '', cpp: '' })
  const [report, setReport] = useState<JudgeReportView | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [runningSamples, setRunningSamples] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [openingPdf, setOpeningPdf] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)

  const [activeTab, setActiveTab] = useState<'samples' | 'custom'>('samples')
  const [customStdin, setCustomStdin] = useState<string>('')
  const [customResult, setCustomResult] = useState<CustomTestResult | null>(null)
  const [runningCustom, setRunningCustom] = useState(false)

  // Mirrors for the keyboard handler so `Ctrl+Enter` never reads a stale value.
  // The agent handle is mirrored too: `runSamples` has no reactive dependencies,
  // so reading `localAgent` from its closure would freeze the first render's
  // 'disconnected' status and the local path would never be taken.
  const stateRef = useRef({ language, sources, problem, localAgent, activeTab, customStdin })
  const busyRef = useRef(false)

  useEffect(() => {
    stateRef.current = { language, sources, problem, localAgent, activeTab, customStdin }
  })

  /* ---------------------------------------------------------------- load */
  // `<ProblemSolver key={problemId} />` remounts per problem, so the state
  // already starts empty and the effect only has to fill it in.
  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const detail = await api.getProblem(problemId)
        if (cancelled) return
        setProblem(detail)

        // Restore the student's unsaved buffers when we have them.
        const draft = getDraft(problemId)
        if (draft) {
          setLanguage(draft.language)
          setSources(draft.sources)
        } else {
          setLanguage('c')
          setSources({ c: detail.starter_code_c, cpp: detail.starter_code_cpp })
        }

        // Prefill custom test input with first sample case if available
        const firstSample = detail.test_cases.find((tc) => tc.is_sample)
        if (firstSample) {
          setCustomStdin((prev) => (prev ? prev : firstSample.input))
        }
      } catch (reason) {
        if (!cancelled) {
          setLoadError(reason instanceof ApiError ? reason.detail : String(reason))
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [problemId, getDraft])

  /* --------------------------------------------------------------- drafts */
  useEffect(() => {
    if (!problem) return
    setDraft(problemId, { language, sources })
  }, [problem, problemId, language, sources, setDraft])

  const code = sources[language]

  const setCode = useCallback((value: string) => {
    setSources((previous) => ({ ...previous, [stateRef.current.language]: value }))
  }, [])

  const handleEditorChange: OnChange = useCallback(
    (value) => setCode(value ?? ''),
    [setCode],
  )

  const handleEditorMount: OnMount = useCallback((instance) => {
    instance.focus()
  }, [])

  /** Bring back the untouched starter code of the current language. */
  const resetToStarter = () => {
    if (!problem) return
    setSources((previous) => ({
      ...previous,
      [language]: language === 'c' ? problem.starter_code_c : problem.starter_code_cpp,
    }))
  }

  /* --------------------------------------------------------- run samples */
  /**
   * Run every sample case without recording anything.
   *
   * Two execution paths, one report shape:
   *
   *  - **Local Agent connected** - a single `run_batch` request compiles the
   *    source once and runs all sample cases on the student's own machine, so a
   *    five-case statement costs one build instead of five, and the classroom
   *    server is not involved at all.
   *  - **Anything else** (no agent, a refused or short batch) - the server path,
   *    one `POST /api/run` per sample, sequentially on purpose: it keeps a single
   *    student from occupying every judge slot while the class is submitting at
   *    the same time.
   */
  const runSamples = useCallback(async (): Promise<void> => {
    const current = stateRef.current
    const detail = current.problem
    const agent = current.localAgent
    if (!detail || busyRef.current) return
    const samples = detail.test_cases.filter((row) => row.is_sample)
    if (samples.length === 0) {
      setNotice('This problem has no sample test cases, so there is nothing to run locally. Use Submit.')
      return
    }

    if (agent.status !== 'connected') {
      setNotice(
        '未連線本機 Local Agent：學生端測試與除錯須於本機編譯執行，伺服器不提供直接編譯測試。請先啟動 CodingPass 本機編譯代理程式 (ws://127.0.0.1:8765)。',
      )
      return
    }

    busyRef.current = true
    setRunningSamples(true)
    setActiveTab('samples')
    setNotice(null)
    setProgress({ done: 0, total: samples.length })

    const runs: SampleRun[] = samples.map((testCase, index) => ({
      testCase,
      index,
      response: null,
      error: null,
      passed: false,
    }))
    const source = current.sources[current.language]

    try {
      const responses = await agent.runBatchLocally({
        code: source,
        language: current.language,
        stdin_list: samples.map((testCase) => testCase.input),
        timeout_sec: Math.max(1, Math.ceil(detail.time_limit_sec)),
      })
      if (responses.length === samples.length) {
        samples.forEach((testCase, index) => {
          const response = responses[index]
          runs[index] = {
            ...runs[index],
            response,
            passed: response.status === 'success' && outputsMatch(response.stdout, testCase.expected_output),
          }
        })
        setProgress({ done: samples.length, total: samples.length })
        setReport(fromSampleRuns(runs, LOCAL_RUN_HEADING))
      } else {
        setNotice(`本機 Local Agent 僅回傳 ${responses.length}/${samples.length} 筆範例執行結果。`)
      }
    } catch (localErr) {
      setNotice(`本機 Local Agent 執行失敗：${localErr instanceof Error ? localErr.message : String(localErr)}`)
    } finally {
      setProgress(null)
      setRunningSamples(false)
      busyRef.current = false
    }
  }, [])

  /* -------------------------------------------------------------- submit */
  const submit = useCallback(async (): Promise<void> => {
    const current = stateRef.current
    const detail = current.problem
    if (!detail || busyRef.current) return

    busyRef.current = true
    setSubmitting(true)
    setActiveTab('samples')
    setNotice(null)
    try {
      const result = await api.submit(
        detail.id,
        current.language,
        current.sources[current.language],
        submissionTimeout(detail),
      )
      setReport(fromSubmissionResult(result))
    } catch (reason) {
      setNotice(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setSubmitting(false)
      busyRef.current = false
    }
  }, [])

  /* --------------------------------------------------- run custom input */
  /**
   * Run the current code with custom standard input.
   *
   * Executes locally on the student's machine via CodingPass Local Agent.
   * Server-side compilation is not allowed without submission.
   */
  const runCustomInput = useCallback(async (): Promise<void> => {
    const current = stateRef.current
    const detail = current.problem
    const agent = current.localAgent
    if (!detail || busyRef.current) return

    if (agent.status !== 'connected') {
      setNotice(
        '未連線本機 Local Agent：學生端測試與除錯須於本機編譯執行，伺服器不提供直接編譯測試。請先啟動 CodingPass 本機編譯代理程式 (ws://127.0.0.1:8765)。',
      )
      setCustomResult({
        response: null,
        error: '未連線本機 Local Agent：伺服器不開放直接編譯，需啟動 Local Agent 進行本機編譯執行。',
        engine: 'local',
      })
      return
    }

    busyRef.current = true
    setRunningCustom(true)
    setNotice(null)
    setActiveTab('custom')

    const source = current.sources[current.language]
    const timeoutSec = Math.max(1, Math.ceil(detail.time_limit_sec))

    try {
      const result = await agent.runLocally({
        code: source,
        language: current.language,
        stdin: customStdin,
        timeout_sec: timeoutSec,
      })

      setCustomResult({
        response: result,
        error: null,
        engine: 'local',
      })
    } catch (err) {
      setCustomResult({
        response: null,
        error: `本機編譯執行失敗：${err instanceof Error ? err.message : String(err)}`,
        engine: 'local',
      })
    } finally {
      setRunningCustom(false)
      busyRef.current = false
    }
  }, [customStdin])

  useCtrlEnter(() => {
    if (stateRef.current.activeTab === 'custom') {
      void runCustomInput()
    } else {
      void runSamples()
    }
  })

  /* ------------------------------------------------------ statement PDF */
  /**
   * Stream the pack's statement PDF into a new browser tab.
   *
   * The endpoint is JWT-guarded, so the file arrives as a blob and is shown via
   * an object URL rather than a plain link. If the popup blocker swallows the
   * tab the blob is already in memory, so say so instead of failing silently.
   */
  const openStatementPdf = useCallback(async (): Promise<void> => {
    const detail = stateRef.current.problem
    if (!detail || openingPdf) return
    setOpeningPdf(true)
    setNotice(null)
    try {
      const blob = await api.downloadProblemPdf(detail.id)
      const url = URL.createObjectURL(blob)
      const opened = window.open(url, '_blank')
      if (!opened) setNotice('Your browser blocked the new tab - allow pop-ups to read the PDF.')
      // Keep the URL alive long enough for the new tab to load the bytes.
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (reason) {
      setNotice(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setOpeningPdf(false)
    }
  }, [openingPdf])

  /* ------------------------------------------------------------- render */
  const samples = useMemo(
    () => (problem ? problem.test_cases.filter((row) => row.is_sample) : []),
    [problem],
  )
  const ownerCases = useMemo(
    () => (problem ? problem.test_cases.filter((row) => !row.is_sample) : []),
    [problem],
  )
  const isOwner = Boolean(problem && user && (user.role === 'admin' || problem.teacher_id === user.id))

  if (loadError) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <SolverHeader title="Problem" onBack={onBack} />
        <div className="p-4">
          <ErrorNotice message={loadError} onRetry={onBack} />
        </div>
      </div>
    )
  }

  if (!problem) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <SolverHeader title="Loading problem…" onBack={onBack} />
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-500">
          <Spinner />
          Fetching the statement and sample cases…
        </div>
      </div>
    )
  }

  const busy = runningSamples || submitting || runningCustom
  const tone = report ? VERDICT_TONE[report.verdict] ?? VERDICT_TONE.runtime_error : null

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Problem header ------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-800 px-3 py-2.5">
        <button
          type="button"
          onClick={onBack}
          title="Back to the problem list"
          className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Problems
        </button>

        <div className="min-w-0">
          <h2 className="flex items-center gap-2 truncate text-sm font-semibold text-white">
            <span className="truncate">{problem.title}</span>
            <DifficultyBadge difficulty={problem.difficulty} />
          </h2>
          <p className="truncate text-[11px] text-slate-500">
            #{problem.id} · by {problem.teacher_name || 'staff'} ·{' '}
            {problem.classroom_id === null ? 'private bank' : `class #${problem.classroom_id}`}
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <MetaChip icon={<Clock className="h-3 w-3" aria-hidden="true" />}>
            {problem.time_limit_sec}s / case
          </MetaChip>
          <MetaChip icon={<Gauge className="h-3 w-3" aria-hidden="true" />}>
            {problem.memory_limit_mb}MB
          </MetaChip>
          <MetaChip icon={<Layers className="h-3 w-3" aria-hidden="true" />}>
            {problem.sample_count} sample
          </MetaChip>
          <MetaChip icon={<EyeOff className="h-3 w-3" aria-hidden="true" />}>
            {problem.hidden_count} hidden
          </MetaChip>
          {problem.is_exam ? (
            <span className="rounded-full border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-rose-300">
              🔒 考試模式
            </span>
          ) : null}
          {problem.has_statement_pdf ? (
            <button
              type="button"
              onClick={() => void openStatementPdf()}
              disabled={openingPdf}
              title="Open the official statement PDF in a new tab"
              className={buttonClass('ghost', 'px-2 py-1 text-[10px]')}
            >
              {openingPdf ? (
                <Spinner className="h-3 w-3" />
              ) : (
                <FileText className="h-3 w-3" aria-hidden="true" />
              )}
              <span>📄 題目 PDF</span>
            </button>
          ) : null}
          {report && tone ? (
            <span className={`rounded-md border px-2 py-1 font-mono text-[10px] font-semibold ${tone.pill}`}>
              last {formatScore(report.score)} · {countText(report.passed, report.total)}
            </span>
          ) : null}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 lg:flex-row lg:gap-4 lg:overflow-hidden lg:p-4">
        {/* Statement --------------------------------------------------- */}
        <section
          onCopy={(e) => {
            if (problem.is_exam) {
              e.preventDefault()
            }
          }}
          className={`flex min-h-[18rem] flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 lg:min-h-0 lg:basis-[42%] ${
            problem.is_exam ? 'select-none' : ''
          }`}
        >
          <div className="flex shrink-0 items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-2">
            <BookOpen className="h-4 w-4 text-sky-400" aria-hidden="true" />
            <span className="text-xs font-semibold text-slate-300">Problem statement</span>
            {isOwner ? (
              <span className="ml-auto rounded bg-sky-500/15 px-1.5 py-0.5 font-mono text-[10px] text-sky-300">
                owner view
              </span>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
            <Markdown source={problem.description} />

            <div className="mt-4">
              <p className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <Layers className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                Sample test cases
                <span className="font-mono text-[10px] normal-case tracking-normal text-slate-600">
                  ({samples.length})
                </span>
              </p>
              {samples.length === 0 ? (
                <p className="text-[11px] text-slate-500">The teacher has not published sample cases.</p>
              ) : (
                <ul className="space-y-2">
                  {samples.map((testCase, index) => (
                    <SampleCaseRow key={testCase.id} testCase={testCase} index={index} />
                  ))}
                </ul>
              )}
            </div>

            <p className="mt-4 flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-[11px] text-slate-400">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" aria-hidden="true" />
              <span>
                {problem.hidden_count} hidden case{problem.hidden_count === 1 ? '' : 's'} decide the rest of
                the score. Their inputs and expected outputs are never sent to the browser - Submit only
                returns a verdict and a timing for each of them.
              </span>
            </p>

            {isOwner && ownerCases.length > 0 ? (
              <div className="mt-4">
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  Hidden cases (visible to you only)
                </p>
                <ul className="space-y-2">
                  {ownerCases.map((testCase, index) => (
                    <SampleCaseRow key={testCase.id} testCase={testCase} index={index} hidden />
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </section>

        {/* Editor + result --------------------------------------------- */}
        <section className="flex min-h-[26rem] min-w-0 flex-col gap-3 lg:min-h-0 lg:basis-[58%]">
          <div className="flex min-h-[16rem] flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 lg:flex-1">
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-800/80 bg-slate-900/70 px-3 py-2">
              <span className="font-mono text-xs text-slate-300">
                solution.{SOURCE_EXTENSION[language]}
              </span>
              <Segmented
                options={LANGUAGE_OPTIONS}
                value={language}
                onChange={setLanguage}
                ariaLabel="Solution language"
                size="sm"
              />
              <button
                type="button"
                onClick={resetToStarter}
                title="Restore the starter code for this language"
                className={buttonClass('ghost', 'px-2 py-1 text-[11px]')}
              >
                <RotateCcw className="h-3 w-3" aria-hidden="true" />
                Starter
              </button>

              <div className="ml-auto flex flex-wrap items-center gap-2">
                {progress ? (
                  <span className="font-mono text-[10px] text-slate-400">
                    case {progress.done}/{progress.total}
                  </span>
                ) : null}
                <button
                  type="button"
                  data-testid="btn-custom-test"
                  onClick={() => {
                    setActiveTab('custom')
                    void runCustomInput()
                  }}
                  disabled={busy}
                  title="以自訂標準輸入 (Stdin) 執行目前程式碼 (不計分、不上傳)"
                  className={buttonClass(
                    activeTab === 'custom' ? 'subtle' : 'ghost',
                    `text-xs gap-1.5 ${
                      activeTab === 'custom'
                        ? 'border border-indigo-500/60 bg-indigo-500/15 text-indigo-300'
                        : 'text-slate-300 hover:text-white'
                    }`,
                  )}
                >
                  {runningCustom ? (
                    <Spinner className="h-3.5 w-3.5 text-indigo-400" />
                  ) : (
                    <Terminal className="h-3.5 w-3.5 text-indigo-400" aria-hidden="true" />
                  )}
                  <span>自訂測資</span>
                </button>
                <button
                  type="button"
                  onClick={() => void runSamples()}
                  disabled={busy}
                  title={
                    localAgent.status === 'connected'
                      ? '由本地 CodingPass Local Agent 即時編譯執行範例測資 (Ctrl+Enter)'
                      : '未連線 Local Agent：需先啟動本機代理程式才能編譯執行（伺服器端不開放直接編譯）'
                  }
                  className={buttonClass('ghost', 'text-xs')}
                >
                  {runningSamples ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Play className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true" />
                  )}
                  <span>
                    {runningSamples
                      ? 'Running…'
                      : localAgent.status === 'connected'
                      ? 'Run Samples (Local)'
                      : 'Run Samples (需 Local Agent)'}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => void submit()}
                  disabled={busy}
                  title={`Grade against all ${problem.sample_count + problem.hidden_count} cases`}
                  className={buttonClass('primary', 'text-xs')}
                >
                  {submitting ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Send className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                  <span>{submitting ? 'Judging…' : 'Submit Solution'}</span>
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1">
              <Editor
                path={`file:///problem-${problem.id}/solution.${SOURCE_EXTENSION[language]}`}
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
              <span>{LANGUAGE_LABEL[language]}</span>
              <span className="text-slate-700">|</span>
              <span>{language === 'c' ? 'gcc -O2 -Wall -lm' : 'g++ -O2 -std=c++17 -Wall -lm'}</span>
              <span className="text-slate-700">|</span>
              <span>
                {problem.sample_count + problem.hidden_count} cases ·{' '}
                {user?.role === 'student' ? 'recorded' : 'trial run'}
              </span>
              <span className="ml-auto text-slate-600">
                Ctrl/Cmd + Enter = {activeTab === 'custom' ? '執行自訂測資' : 'Run Samples'}
              </span>
            </div>
          </div>

          {/* Result Section with Tabs ----------------------------------- */}
          <div className="flex flex-col min-h-[14rem] lg:max-h-[50%] lg:shrink-0 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
            {/* Tab header */}
            <div className="flex shrink-0 items-center justify-between border-b border-slate-800 bg-slate-900/80 px-2.5 py-1.5 text-xs">
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  data-testid="tab-samples"
                  onClick={() => setActiveTab('samples')}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-medium transition-colors ${
                    activeTab === 'samples'
                      ? 'bg-slate-800 text-slate-100 shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Layers className="h-3.5 w-3.5 text-emerald-400" />
                  <span>範例評測與成績</span>
                  {report ? (
                    <span className={`ml-1 rounded px-1.5 py-0.2 font-mono text-[10px] ${tone?.pill}`}>
                      {report.passed}/{report.total}
                    </span>
                  ) : null}
                </button>

                <button
                  type="button"
                  data-testid="tab-custom"
                  onClick={() => setActiveTab('custom')}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-medium transition-colors ${
                    activeTab === 'custom'
                      ? 'bg-slate-800 text-slate-100 shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Terminal className="h-3.5 w-3.5 text-indigo-400" />
                  <span>自訂測資偵錯</span>
                  {customResult?.response ? (
                    <span
                      className={`ml-1 rounded px-1.5 py-0.2 font-mono text-[10px] ${
                        customResult.response.status === 'success'
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : 'bg-rose-500/20 text-rose-300'
                      }`}
                    >
                      {customResult.response.status}
                    </span>
                  ) : null}
                </button>
              </div>

              <div className="pr-1 font-mono text-[10px] text-slate-500">
                {activeTab === 'custom' ? (
                  localAgent.status === 'connected' ? (
                    <span className="text-emerald-400">⚡ 本機 Local Agent 編譯</span>
                  ) : (
                    <span className="text-rose-400">🔴 Local Agent 未連線（需本機編譯）</span>
                  )
                ) : (
                  <span>{problem.sample_count} sample · {problem.hidden_count} hidden</span>
                )}
              </div>
            </div>

            {/* Tab content */}
            <div className="min-h-0 flex-1 overflow-y-auto">
              {activeTab === 'custom' ? (
                <CustomTestPanel
                  stdin={customStdin}
                  onStdinChange={setCustomStdin}
                  onRun={runCustomInput}
                  running={runningCustom}
                  result={customResult}
                  sampleCases={samples}
                  localAgentConnected={localAgent.status === 'connected'}
                  timeLimitSec={problem.time_limit_sec}
                />
              ) : report ? (
                <div className="h-full min-h-0">
                  <ResultPanel report={report} notice={notice} />
                </div>
              ) : (
                <div className="flex h-full min-h-[12rem] flex-col items-center justify-center p-6 text-center">
                  {notice ? (
                    <div className="mb-4 flex max-w-lg items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-xs text-amber-200 shadow-sm">
                      <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
                      <span className="text-left leading-relaxed">{notice}</span>
                    </div>
                  ) : null}
                  <EmptyState
                    icon={<Trophy className="h-8 w-8" aria-hidden="true" />}
                    title="No result yet"
                    hint={
                      samples.length > 0
                        ? 'Run the samples for instant feedback (需 Local Agent)，切換至自訂測資進行除錯，或點擊 Submit 正式評測。'
                        : 'Submit to grade this solution against the problem’s test data.'
                    }
                  />
                </div>
              )}
            </div>
          </div>

          {/* AI tutor ---------------------------------------------------- */}
          {problem.is_exam ? (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-center text-xs text-slate-400">
              <span className="font-semibold text-rose-300">🔒 學生考試模式已啟動</span>
              <p className="mt-1 text-[11px] text-slate-500">考場環境已停用 AI Tutor 即時解析與提示功能。</p>
            </div>
          ) : (
            <AITutorPanel
              code={code}
              language={language}
              status={report?.verdict}
              compileOutput={report?.compileOutput}
              problemId={problem.id}
              compact
            />
          )}
        </section>
      </div>

      {problem.is_exam && user?.role === 'student' ? (
        <ExamLockdownOverlay problem={problem} onForceSubmitted={onBack} />
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */

function SolverHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="flex items-center gap-3 border-b border-slate-800 px-3 py-2.5">
      <button type="button" onClick={onBack} className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}>
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Problems
      </button>
      <h2 className="truncate text-sm font-semibold text-white">{title}</h2>
    </div>
  )
}

/** One sample (or owner-visible hidden) case rendered as an IO card. */
function SampleCaseRow({
  testCase,
  index,
  hidden = false,
}: {
  testCase: TestCasePublic
  index: number
  hidden?: boolean
}) {
  return (
    <li className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          {hidden ? `Hidden #${index + 1}` : `Sample #${index + 1}`}
        </span>
        <CopyButton value={testCase.input} label="copy stdin" className="ml-auto" />
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-600">input</p>
          <pre className={PRE_CLASS}>
            {testCase.input.length ? testCase.input : <span className="italic text-slate-600">(empty)</span>}
          </pre>
        </div>
        <div>
          <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-600">expected</p>
          <pre className={`${PRE_CLASS} text-emerald-200/90`}>
            {testCase.expected_output.length ? (
              testCase.expected_output
            ) : (
              <span className="italic text-slate-600">(empty)</span>
            )}
          </pre>
        </div>
      </div>
    </li>
  )
}

export default ProblemSolver
