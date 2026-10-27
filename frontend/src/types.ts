/**
 * Wire types shared between the CodingPass student UI and the FastAPI judge
 * (`backend/main.py`). Keep them in sync with the Pydantic schemas there.
 */

/** Languages accepted by `POST /api/run`. */
export type Language = 'c' | 'cpp'

/** Terminal judge states reported in `RunResponse.status`. */
export type JudgeStatus =
  | 'success'
  | 'compile_error'
  | 'time_limit_exceeded'
  | 'runtime_error'
  | 'internal_error'

/** `POST /api/run` request body. */
export interface RunRequest {
  /** Complete source file to compile and run. */
  code: string
  /** Target language. */
  language: Language
  /** Text piped to the program on stdin. */
  stdin?: string
}

/** `POST /api/run` response body (HTTP 200 for every judged outcome). */
export interface RunResponse {
  status: JudgeStatus
  /** Bytes written to stdout by the submission. */
  stdout: string
  /** Bytes written to stderr by the submission. */
  stderr: string
  /** Compiler diagnostics; non-empty when `status === 'compile_error'`. */
  compile_output: string
  /** Process exit code, or `null` when the process never started / was killed. */
  exit_code: number | null
  /** Wall-clock execution time in milliseconds. */
  execution_time_ms: number
  /** Set when the process died from a signal, e.g. `SIGSEGV`. */
  signal_name: string | null
  /** Wall-clock compile time in milliseconds. */
  compile_time_ms: number
  /** Language the result belongs to. */
  language: string
  /** True when the wall-clock or CPU limit was hit. */
  timed_out: boolean
  /** True when stdout and/or stderr were truncated by the judge. */
  truncated: boolean
}

/** `GET /api/health` response body. */
export interface HealthResponse {
  status: 'ok' | 'degraded'
  gcc_version: string
  /** Serialised with a literal key because of the `+` in `g++`. */
  'g++_version': string
  languages: string[]
  limits: {
    timeout_seconds?: number
    compile_timeout_seconds?: number
    memory_limit_bytes?: number
    file_size_limit_bytes?: number
    max_output_bytes?: number
    languages?: string[]
  }
}

/** Client-side view of the judge API liveness. */
export type BackendStatus = 'checking' | 'online' | 'degraded' | 'offline'

/** Health state rendered by the header pill. */
export interface BackendHealth {
  status: BackendStatus
  /** Short human readable explanation (used as the pill tooltip). */
  detail: string
  /** Last successful `/api/health` payload, if any. */
  info: HealthResponse | null
}


/** File extension the judge compiles a language with. */
export const SOURCE_EXTENSION: Record<Language, string> = {
  c: 'c',
  cpp: 'cpp',
}

/** Human readable language names. */
export const LANGUAGE_LABEL: Record<Language, string> = {
  c: 'C',
  cpp: 'C++',
}

/** Monaco model id for a language (`c` and `cpp` are built into monaco). */
export const MONACO_LANGUAGE: Record<Language, string> = {
  c: 'c',
  cpp: 'cpp',
}

/** Format milliseconds for the stats tiles. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(value < 10 ? 2 : 1)} ms`
}

/* ==========================================================================
 * Phase 2 / 3 - accounts, classrooms, problem bank and graded submissions.
 * -------------------------------------------------------------------------- */

/** Account roles mirrored from `models.Role`. */
export type Role = 'admin' | 'teacher' | 'student'

/** Problem difficulty mirrored from `models.Difficulty`. */
export type Difficulty = 'easy' | 'medium' | 'hard'

/**
 * Aggregate verdict of a graded submission **and** of a single test case
 * (`SubmissionStatusLiteral` / `CaseStatusLiteral` share the vocabulary).
 */
export type Verdict =
  | 'accepted'
  | 'wrong_answer'
  | 'time_limit_exceeded'
  | 'compile_error'
  | 'runtime_error'

/** `UserOut` - public view of an account. */
export interface User {
  id: number
  username: string
  email: string
  full_name: string
  role: Role
  created_at: string
}

/** `TokenResponse` - body of `POST /api/auth/login`. */
export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  /** Token lifetime in seconds. */
  expires_in: number
  user: User
}

/** Body of `POST /api/auth/register`. */
export interface RegisterRequest {
  username: string
  email: string
  password: string
  full_name?: string
  /** Only an admin caller may raise this above `student`. */
  role?: Role
}

/** `ClassroomOut` - one row of `GET /api/classrooms`. */
export interface Classroom {
  id: number
  name: string
  /** Invite code students use to enrol (e.g. `CS101`). */
  code: string
  description: string
  teacher_id: number
  teacher_name: string
  created_at: string
  student_count: number
  problem_count: number
}

/** `ClassroomMemberOut` - one enrolled student. */
export interface ClassroomMember {
  id: number
  username: string
  full_name: string
  email: string
  joined_at: string
}

/** `ClassroomDetail` - `GET /api/classrooms/{id}`. */
export interface ClassroomDetail extends Classroom {
  teacher_username: string
  students: ClassroomMember[]
  problems: ProblemSummary[]
}

/** `TestCasePublic` - a sample case as shown to students. */
export interface TestCasePublic {
  id: number
  input: string
  expected_output: string
  is_sample: boolean
}

/** `TestCaseIn` - a test case as authored by a teacher. */
export interface TestCaseDraft {
  input: string
  expected_output: string
  is_sample: boolean
}

/** `ProblemSummary` - compact row used in lists. */
export interface ProblemSummary {
  id: number
  title: string
  difficulty: Difficulty
  time_limit_sec: number
  memory_limit_mb: number
  classroom_id: number | null
  teacher_id: number
  teacher_name: string
  created_at: string
  sample_count: number
  hidden_count: number
  problem_code?: string | null
  has_ref_code?: boolean
  has_wording_rules?: boolean
  has_random_generator?: boolean
  has_statement_pdf?: boolean
  pdf_path?: string | null
}

/** `ProblemDetail` - `GET /api/problems/{id}` (samples only for students). */
export interface ProblemDetail extends ProblemSummary {
  description: string
  starter_code_c: string
  starter_code_cpp: string
  test_cases: TestCasePublic[]
  wording_rules?: string | null
}

/** `ProblemImportResult` - response of `POST /api/problems/import-pack`. */
export interface ProblemImportResult extends ProblemDetail {
  pack_name: string
  pack_source: string
  sample_case_count: number
  random_case_count: number
  warnings: string[]
}

/** `ProblemListResponse`. */
export interface ProblemListResponse {
  total: number
  count: number
  problems: ProblemSummary[]
}

/** Body of `POST /api/problems`. */
export interface ProblemCreateRequest {
  title: string
  description: string
  difficulty: Difficulty
  time_limit_sec: number
  memory_limit_mb: number
  classroom_id: number | null
  starter_code_c: string | null
  starter_code_cpp: string | null
  test_cases: TestCaseDraft[]
}

/** `CaseVerdict` - verdict for one test case inside a graded submission. */
export interface CaseVerdict {
  test_case_id: number
  is_sample: boolean
  passed: boolean
  status: Verdict
  execution_time_ms: number
  exit_code: number | null
  signal_name: string | null
  truncated: boolean
  presentation_warning?: boolean
  /** `null` for hidden cases seen by a student. */
  input: string | null
  expected_output: string | null
  stdout: string | null
  stderr: string | null
}

/** `HiddenSummary`. */
export interface HiddenSummary {
  total: number
  passed: number
  failed: number
}

/** Rubric breakdown (compile 10 + sample 10 + random 30 + logic 40 + quality 10). */
export interface RubricScores {
  compile_score: number
  sample_score: number
  random_score: number
  logic_score: number
  quality_score: number
  total: number
}

/** Minimal Patch Probe diagnostic report. */
export interface PatchProbeReport {
  needed: boolean
  patch_success: boolean
  probed_logic_score: number
  details: string
  baseline_status?: string
  applied_fixes?: string[]
  attempted_fixes?: string[]
  cases_tested?: number
  cases_passed?: number
}

/** `SubmissionResult` - full grading report of `POST /api/problems/{id}/submit`. */
export interface SubmissionResult {
  /** `null` for a teacher/admin trial run. */
  submission_id: number | null
  problem_id: number
  student_id: number
  language: string
  status: Verdict
  /** Passed cases as a percentage, 0-100. */
  score: number
  passed_count: number
  total_count: number
  /** Slowest single case. */
  execution_time_ms: number
  compile_time_ms: number
  compile_output: string
  time_limit_sec: number
  memory_limit_mb: number
  trial_run: boolean
  results: CaseVerdict[]
  hidden_summary: HiddenSummary

  /* -- Stage 3 & 4 Rubric, Formatting and Probe -- */
  rubric?: RubricScores
  presentation_warning?: boolean
  wording_pass?: boolean
  wording_violations?: string[]
  patch_probe?: PatchProbeReport | null
}

/** `SubmissionOut` - one row of `GET /api/submissions`. */
export interface SubmissionRow {
  id: number
  problem_id: number
  problem_title: string
  student_id: number
  student_username: string
  student_name: string
  language: string
  status: Verdict
  score: number
  passed_count: number
  total_count: number
  execution_time_ms: number
  created_at: string

  /* -- Stage 4: 100-point rubric breakdown -- */
  rubric_total?: number
  rubric?: RubricScores
  presentation_warning?: boolean
  wording_pass?: boolean
  wording_violations?: string[]

  /* -- Phase 4: AI tutor + teacher review (`null` until somebody reviews) -- */
  /** Markdown review produced by the AI tutor. */
  ai_feedback?: string | null
  /** Advisory score suggested by the AI (0-100). */
  ai_suggested_score?: number | null
  /** Model that produced `ai_feedback`. */
  ai_model?: string | null
  ai_reviewed_at?: string | null
  /** Teacher's written feedback. */
  teacher_feedback?: string | null
  /** Teacher's manual score (0-100). */
  teacher_score?: number | null
  /** Teacher/admin account that graded the attempt. */
  reviewed_by_id?: number | null
  reviewed_at?: string | null
}

/** `SubmissionListResponse`. */
export interface SubmissionListResponse {
  total: number
  count: number
  submissions: SubmissionRow[]
}

/** `SubmissionDetailOut` - one recorded attempt including its source code. */
export interface SubmissionDetail extends SubmissionRow {
  /** Complete source file that was submitted and graded. */
  code: string
}

/** `AIStatusResponse` - body of `GET /api/ai/status`. */
export interface AIStatusResponse {
  /** True when the configured vLLM tutor answers a probe request. */
  available: boolean
  provider: string
  model: string
  base_url: string
  detail: string
}

/** Body of `POST /api/ai/analyze` (ad-hoc review, nothing is stored). */
export interface AIAnalyzeRequest {
  code: string
  language: Language
  /** Judge verdict for this source, if known (`'unknown'` otherwise). */
  status?: string
  compile_output?: string
  stderr?: string
  /** Attach the problem statement as context; requires authentication. */
  problem_id?: number | null
}

/** `AIAnalyzeResponse` - structured review produced by the AI tutor. */
export interface AIAnalyzeResponse {
  summary: string
  error_analysis: string
  time_complexity: string
  space_complexity: string
  suggestions: string[]
  /** Advisory score suggested by the AI (0-100). */
  suggested_score: number
  /** The full review as Markdown (what gets persisted on a submission). */
  feedback_markdown: string
  model: string
  /** False when the heuristic reviewer answered instead of the LLM. */
  ai_powered: boolean
  /** Why the heuristic fallback was used, when applicable. */
  detail?: string
}

/** `SubmissionAIReviewResponse` - stored AI review plus its structured analysis. */
export interface SubmissionAIReviewResponse extends SubmissionDetail {
  analysis: AIAnalyzeResponse
}

/** Body of `PATCH /api/submissions/{id}/review` (teacher/admin grading). */
export interface TeacherReviewRequest {
  teacher_score?: number | null
  teacher_feedback?: string | null
}

/** One suspicious student pair reported by the structural plagiarism screen. */
export interface PlagiarismPair {
  student_a: string
  student_b: string
  submission_id_a: number
  submission_id_b: number
  language: string
  combined_similarity: number
  ast_similarity: number
  token_similarity: number
  ngram_jaccard: number
  ref_similarity_a?: number | null
  ref_similarity_b?: number | null
  flagged: boolean
  exemption?: string | null
}

/** `PlagiarismReport` - response of `GET /api/classrooms/{cid}/problems/{pid}/plagiarism`. */
export interface PlagiarismReport {
  problem_id: number
  problem_title: string
  problem_code?: string | null
  classroom_id: number
  classroom_name: string
  threshold: number
  reference_available: boolean
  languages: string[]
  submissions_compared: number
  students_compared: number
  pair_count: number
  flagged_count: number
  exempt_count: number
  skipped_trivial: number
  truncated: boolean
  pairs: PlagiarismPair[]
}

/** Tabs of the application shell. */
export type AppTab = 'playground' | 'problems' | 'classrooms' | 'submissions'
