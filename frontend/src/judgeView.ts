/**
 * View model for judge results.
 *
 * Two very different payloads must share one result panel:
 *  - `SubmissionResult` from `POST /api/problems/{id}/submit` (graded, includes
 *    hidden-case verdicts whose contents are deliberately redacted), and
 *  - local "Run Samples" runs built from `POST /api/run` per sample case
 *    (never recorded, full stdout available).
 *
 * Both are normalised into `JudgeReportView` so the panel - and its verdict
 * wording - is identical for a trial run and a graded submission.
 */
import type {
  CaseVerdict,
  HiddenSummary,
  PatchProbeReport,
  RubricScores,
  RunResponse,
  SubmissionResult,
  TestCasePublic,
  Verdict,
} from './types'

/** One test case row in the result panel. */
export interface CaseRowView {
  key: string
  /** "Sample #1", "Hidden #3" … */
  label: string
  isSample: boolean
  /** True when the student may not see input / expected / actual output. */
  redacted: boolean
  passed: boolean
  status: Verdict
  timeMs: number
  presentationWarning?: boolean
  input: string | null
  expected: string | null
  got: string | null
  stderr: string | null
  exitCode: number | null
  signalName: string | null
  truncated: boolean
}

/** The whole report rendered by `<ResultPanel />`. */
export interface JudgeReportView {
  verdict: Verdict
  score: number
  passed: number
  total: number
  /** Slowest single case, in ms. */
  timeMs: number
  compileTimeMs: number
  compileOutput: string
  hidden: HiddenSummary | null
  /** True for a teacher/admin submit, or for a local sample run. */
  notRecorded: boolean
  /** Headline text: "Graded submission" / "Sample run - not recorded". */
  heading: string
  rows: CaseRowView[]
  createdAt: string | null
  submissionId: number | null
  language: string

  /* -- Stage 4 Rubric Breakdown & Formatting Warnings -- */
  rubric?: RubricScores
  presentationWarning?: boolean
  wordingPass?: boolean
  wordingViolations?: string[]
  patchProbe?: PatchProbeReport | null
}

/** Map a graded submission to the panel view model. */
export function fromSubmissionResult(result: SubmissionResult): JudgeReportView {
  let sampleSeen = 0
  let hiddenSeen = 0

  const rows: CaseRowView[] = result.results.map((verdict: CaseVerdict, index) => {
    const label = verdict.is_sample
      ? `Sample #${++sampleSeen}`
      : `Hidden #${++hiddenSeen}`
    const redacted = !verdict.is_sample && verdict.input === null
    return {
      key: `${verdict.test_case_id}-${index}`,
      label,
      isSample: verdict.is_sample,
      redacted,
      passed: verdict.passed,
      status: verdict.status,
      timeMs: verdict.execution_time_ms,
      presentationWarning: verdict.presentation_warning,
      input: verdict.input,
      expected: verdict.expected_output,
      got: verdict.stdout,
      stderr: verdict.stderr,
      exitCode: verdict.exit_code,
      signalName: verdict.signal_name,
      truncated: verdict.truncated,
    }
  })

  return {
    verdict: result.status,
    score: result.score,
    passed: result.passed_count,
    total: result.total_count,
    timeMs: result.execution_time_ms,
    compileTimeMs: result.compile_time_ms,
    compileOutput: result.compile_output,
    hidden: result.hidden_summary.total > 0 ? result.hidden_summary : null,
    notRecorded: result.trial_run,
    heading: result.trial_run ? 'Trial run - not recorded' : 'Graded submission',
    rows,
    createdAt: null,
    submissionId: result.submission_id,
    language: result.language,
    rubric: result.rubric,
    presentationWarning: result.presentation_warning,
    wordingPass: result.wording_pass,
    wordingViolations: result.wording_violations,
    patchProbe: result.patch_probe,
  }
}

/** One local sample run, produced by the solver while "Run Samples" executes. */
export interface SampleRun {
  testCase: TestCasePublic
  index: number
  /** `null` while the case has not finished yet. */
  response: RunResponse | null
  /** Transport failure message when `/api/run` itself failed. */
  error: string | null
  passed: boolean
}

/** Aggregate local sample runs into the same panel view model. */
export function fromSampleRuns(runs: SampleRun[], heading = 'Sample run - not recorded'): JudgeReportView {
  const passed = runs.filter((run) => run.passed).length
  const total = runs.length
  const slowest = runs.reduce((best, run) => Math.max(best, run.response?.execution_time_ms ?? 0), 0)
  const compileTime = runs.reduce((best, run) => Math.max(best, run.response?.compile_time_ms ?? 0), 0)
  const compileOutput = runs.find((run) => run.response && run.response.compile_output)?.response
    ?.compile_output

  let verdict: Verdict = passed === total && total > 0 ? 'accepted' : 'wrong_answer'
  if (runs.some((run) => run.response?.status === 'compile_error')) verdict = 'compile_error'
  else if (runs.some((run) => run.response?.status === 'time_limit_exceeded')) {
    verdict = 'time_limit_exceeded'
  } else if (runs.some((run) => run.response && run.response.status !== 'success' && run.response.status !== 'compile_error' && run.response.status !== 'time_limit_exceeded')) {
    verdict = 'runtime_error'
  }

  const rows: CaseRowView[] = runs.map((run) => {
    const response = run.response
    let status: Verdict = 'wrong_answer'
    if (response) {
      if (response.status === 'success') status = run.passed ? 'accepted' : 'wrong_answer'
      else if (response.status === 'internal_error') status = 'runtime_error'
      else status = response.status
    } else if (run.error) {
      status = 'runtime_error'
    }

    return {
      key: `sample-${run.testCase.id}-${run.index}`,
      label: `Sample #${run.index + 1}`,
      isSample: true,
      redacted: false,
      passed: run.passed,
      status,
      timeMs: response?.execution_time_ms ?? 0,
      input: run.testCase.input,
      expected: run.testCase.expected_output,
      got: run.error ? '' : (response?.stdout ?? ''),
      stderr: run.error ?? (response?.stderr || response?.compile_output || ''),
      exitCode: response?.exit_code ?? null,
      signalName: response?.signal_name ?? null,
      truncated: response?.truncated ?? false,
    }
  })

  return {
    verdict,
    score: total > 0 ? Math.round((1000 * passed) / total) / 10 : 0,
    passed,
    total,
    timeMs: slowest,
    compileTimeMs: compileTime,
    compileOutput: compileOutput ?? '',
    hidden: null,
    notRecorded: true,
    heading,
    rows,
    createdAt: null,
    submissionId: null,
    language: runs[0]?.response?.language ?? 'c',
  }
}
