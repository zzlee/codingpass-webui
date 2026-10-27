"""Pydantic schemas for the CodingPass API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

try:
    from .compiler import DEFAULT_TIMEOUT_SECONDS, MEMORY_LIMIT_BYTES
    from .config import (
        EMAIL_RE,
        INVITE_CODE_RE,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        PLAGIARISM_MAX_SUBMISSIONS,
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        USERNAME_RE,
        CaseStatusLiteral,
        LanguageLiteral,
        StatusLiteral,
    )
    from .models import (
        Difficulty,
        DifficultyLiteral,
        RoleLiteral,
        SubmissionStatusLiteral,
    )
except ImportError:
    from compiler import DEFAULT_TIMEOUT_SECONDS, MEMORY_LIMIT_BYTES  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        EMAIL_RE,
        INVITE_CODE_RE,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        PLAGIARISM_MAX_SUBMISSIONS,
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        USERNAME_RE,
        CaseStatusLiteral,
        LanguageLiteral,
        StatusLiteral,
    )
    from models import (  # type: ignore[no-redef]
        Difficulty,
        DifficultyLiteral,
        RoleLiteral,
        SubmissionStatusLiteral,
    )


# --------------------------------------------------------------------------- #
# Helpers & Serialisers
# --------------------------------------------------------------------------- #


def _utc_iso(value: datetime) -> str:
    """Render a stored naive-UTC timestamp as an explicit ISO-8601 UTC string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


#: Timestamp field that always serialises with a ``+00:00`` offset.
UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, when_used="json")]


def _lf(value: Optional[str]) -> Optional[str]:
    """Normalise CRLF/CR line endings to LF."""
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


# --------------------------------------------------------------------------- #
# Run / Playground & Service Schemas
# --------------------------------------------------------------------------- #


class RunRequest(BaseModel):
    """A submission plus the input it should be run against."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "#include <stdio.h>\nint main(void){int a,b;"
                    "if(scanf(\"%d %d\",&a,&b)!=2)return 1;"
                    "printf(\"%d\\n\",a+b);return 0;}\n",
                    "language": "c",
                    "stdin": "2 3\n",
                }
            ]
        }
    )

    code: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SOURCE_CHARS,
        description="Complete source file to compile and run.",
    )
    language: LanguageLiteral = Field(..., description="Target language: 'c' or 'cpp'.")
    stdin: Optional[str] = Field(
        default="",
        max_length=MAX_STDIN_CHARS,
        description="Text piped to the program on stdin.",
    )

    @field_validator("code")
    @classmethod
    def _reject_binary_garbage(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("code must not contain NUL bytes")
        return value.replace("\r\n", "\n").replace("\r", "\n")


class RunResponse(BaseModel):
    """Uniform judge payload; failures are reported in-band, not as HTTP errors."""

    status: StatusLiteral
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    exit_code: Optional[int] = None
    execution_time_ms: float = 0.0
    signal_name: Optional[str] = Field(
        default=None, description="Set when the process died from a signal, e.g. 'SIGSEGV'."
    )
    compile_time_ms: float = 0.0
    language: str = ""
    timed_out: bool = False
    truncated: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    gcc_version: str
    gpp_version: str = Field(..., serialization_alias="g++_version")
    languages: list[str] = Field(default_factory=list)
    limits: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


class UserOut(BaseModel):
    """Public view of an account; the password digest is never serialised."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    full_name: str
    role: RoleLiteral
    created_at: UtcDatetime


class UserListResponse(BaseModel):
    """Paginated list of user accounts for admin management."""

    users: list[UserOut]
    total: int


class UserUpdate(BaseModel):
    """Body of ``PATCH /api/users/{id}`` for updating user attributes."""

    full_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, min_length=5, max_length=255)
    role: Optional[RoleLiteral] = None
    password: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=128,
        description="Optional password reset by admin.",
    )

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        email = value.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("email address is not valid")
        return email

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


class PasswordChangeRequest(BaseModel):
    """Body of ``POST /api/auth/change-password``."""

    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    """Body of ``POST /api/auth/register``."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "username": "alice",
                    "email": "alice@example.com",
                    "password": "passw0rd!",
                    "full_name": "Alice Lin",
                }
            ]
        }
    )

    username: str = Field(
        ...,
        min_length=3,
        max_length=32,
        description="Letters, digits, '.', '_' or '-', starting with a letter. Stored lowercase.",
    )
    email: str = Field(..., min_length=5, max_length=255, description="Stored lowercase.")
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="Only the first 72 UTF-8 bytes reach bcrypt.",
    )
    full_name: str = Field(default="", max_length=120)
    role: RoleLiteral = Field(
        default="student",
        description="Public sign-ups are students; only a signed-in admin may create staff.",
    )

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        username = (value or "").strip().lower()
        if not USERNAME_RE.fullmatch(username):
            raise ValueError(
                "username must start with a letter and use only letters, digits, "
                "'.', '_' or '-' (3-32 characters)"
            )
        return username

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        email = (value or "").strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("email address is not valid")
        return email

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str) -> str:
        return (value or "").strip()


class LoginRequest(BaseModel):
    """Body of ``POST /api/auth/login``."""

    username: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Username **or** e-mail address.",
        examples=["student1"],
    )
    password: str = Field(..., min_length=1, max_length=128, examples=["student123"])

    @field_validator("username")
    @classmethod
    def _trim_identifier(cls, value: str) -> str:
        return (value or "").strip().lower()


class TokenResponse(BaseModel):
    """A freshly minted bearer token plus the account it belongs to."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(..., description="Token lifetime in seconds.")
    user: UserOut


# --------------------------------------------------------------------------- #
# Classrooms
# --------------------------------------------------------------------------- #


class ClassroomCreate(BaseModel):
    """Body of ``POST /api/classrooms`` (teacher / admin only)."""

    name: str = Field(..., min_length=2, max_length=120, examples=["Data Structures 202"])
    description: str = Field(default="", max_length=2000)
    code: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=16,
        description="Invite code (A-Z, 0-9, '_', '-'). Auto-generated when omitted.",
    )
    teacher_id: Optional[int] = Field(
        default=None,
        description="Admin only: hand the class to another teacher instead of yourself.",
    )

    @field_validator("name", "description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        code = value.strip().upper()
        if not INVITE_CODE_RE.fullmatch(code):
            raise ValueError(
                "code must be 3-16 characters using A-Z, 0-9, '_' or '-' "
                "(letters are upper-cased for you)"
            )
        return code


class ClassroomJoinRequest(BaseModel):
    """Body of ``POST /api/classrooms/join`` (students only)."""

    code: str = Field(..., min_length=3, max_length=16, examples=["CS101"])

    @field_validator("code")
    @classmethod
    def _normalise_code(cls, value: str) -> str:
        code = (value or "").strip().upper()
        if not INVITE_CODE_RE.fullmatch(code):
            raise ValueError("that is not a valid invite code format")
        return code


class ClassroomOut(BaseModel):
    """Summary row for a classroom."""

    id: int
    name: str
    code: str
    description: str
    teacher_id: int
    teacher_name: str = Field(..., description="Teacher's display name.")
    created_at: UtcDatetime
    student_count: int = 0
    problem_count: int = 0


class ClassroomMemberOut(BaseModel):
    """One enrolled student."""

    id: int
    username: str
    full_name: str
    email: str
    joined_at: UtcDatetime


class ClassroomDetail(ClassroomOut):
    """``GET /api/classrooms/{id}`` payload: summary plus roster and problem list."""

    teacher_username: str = ""
    students: list[ClassroomMemberOut] = Field(default_factory=list)
    problems: list[ProblemSummary] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Problems and Test Cases
# --------------------------------------------------------------------------- #


class TestCaseIn(BaseModel):
    """A test case as supplied by a teacher."""

    input: str = Field(
        default="", max_length=MAX_STDIN_CHARS, description="Text piped to stdin."
    )
    expected_output: str = Field(
        default="", max_length=MAX_EXPECTED_CHARS, description="Exact stdout expected."
    )
    is_sample: bool = Field(
        default=False, description="Samples are shown to students; the rest stay hidden."
    )

    @field_validator("input", "expected_output")
    @classmethod
    def _normalise_newlines(cls, value: str) -> str:
        return _lf(value) or ""


class TestCasePublic(BaseModel):
    """A sample test case as shown to students."""

    id: int
    input: str
    expected_output: str
    is_sample: bool


class ProblemCreate(BaseModel):
    """Body of ``POST /api/problems`` (teacher / admin only)."""

    title: str = Field(..., min_length=3, max_length=200, examples=["Sum of an Array"])
    description: str = Field(
        default="", max_length=MAX_DESCRIPTION_CHARS, description="Markdown statement."
    )
    difficulty: DifficultyLiteral = Field(default=Difficulty.EASY)
    time_limit_sec: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=MIN_TIME_LIMIT_SEC,
        le=MAX_TIME_LIMIT_SEC,
        description="Wall-clock limit applied to **each** test case.",
    )
    memory_limit_mb: int = Field(
        default=MEMORY_LIMIT_BYTES // (1024 * 1024),
        ge=MIN_MEMORY_LIMIT_MB,
        le=MAX_MEMORY_LIMIT_MB,
        description=(
            "Address-space budget (RLIMIT_AS) enforced for every submission to "
            "this problem; defaults to the sandbox ceiling CODE_MEMORY_LIMIT_MB."
        ),
    )
    classroom_id: Optional[int] = Field(
        default=None,
        description="Omit to keep the problem in your private bank.",
    )
    is_exam: bool = Field(default=False, description="Flag problem as examination mode.")
    exam_lock_required: bool = Field(default=False, description="Require local agent lock.")
    max_blur_count: int = Field(default=3, ge=0, le=100, description="Max allowed window blurs.")
    starter_code_c: Optional[str] = Field(default=None, max_length=MAX_SOURCE_CHARS)
    starter_code_cpp: Optional[str] = Field(default=None, max_length=MAX_SOURCE_CHARS)
    test_cases: list[TestCaseIn] = Field(
        default_factory=list, max_length=MAX_TEST_CASES
    )

    @field_validator("title", "description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("starter_code_c", "starter_code_cpp")
    @classmethod
    def _normalise_starter(cls, value: Optional[str]) -> Optional[str]:
        return _lf(value)


class ProblemSummary(BaseModel):
    """Compact problem row used in lists (no description, no test cases)."""

    id: int
    title: str
    difficulty: DifficultyLiteral
    time_limit_sec: float
    memory_limit_mb: int
    classroom_id: Optional[int] = None
    teacher_id: int
    teacher_name: str = ""
    created_at: UtcDatetime
    sample_count: int = 0
    hidden_count: int = 0
    problem_code: Optional[str] = None
    has_ref_code: bool = False
    has_wording_rules: bool = False
    has_random_generator: bool = False
    has_statement_pdf: bool = False
    is_exam: bool = False
    exam_lock_required: bool = False
    max_blur_count: int = 3


# Complete schema rebuild for circular model reference
ClassroomDetail.model_rebuild()


class ProblemDetail(ProblemSummary):
    """``GET /api/problems/{id}`` payload."""

    description: str = ""
    starter_code_c: str = ""
    starter_code_cpp: str = ""
    test_cases: list[TestCasePublic] = Field(default_factory=list)
    pdf_path: Optional[str] = Field(
        default=None,
        description="Server path of the statement PDF, staff only.",
    )
    wording_rules: Optional[str] = Field(
        default=None,
        description="TeDa-Wording rule text. Present only for teacher/admin.",
    )


class ProblemListResponse(BaseModel):
    total: int
    count: int
    problems: list[ProblemSummary]


class ImportPackRequest(BaseModel):
    """Body of ``POST /api/problems/import-pack`` (JSON form)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "directory_path": "/home/zzlee/vibecoding/codingpass-tests/P1001_反轉單字",
                    "classroom_id": 1,
                    "generate_hidden": True,
                    "num_random": 30,
                }
            ]
        }
    )

    directory_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Server-side path to a pack folder or .zip.",
    )
    classroom_id: Optional[int] = Field(
        default=None, ge=1,
        description="Publish to this class; omit to keep the problem in your bank.",
    )
    generate_hidden: bool = Field(
        default=True,
        description="Run TeDa generator x reference dual run to build hidden cases.",
    )
    num_random: int = Field(
        default=30, ge=1, le=MAX_TEST_CASES,
        description="How many random hidden cases to generate.",
    )
    seed: int = Field(
        default=42,
        description="Base seed of the TeDa generator.",
    )

    @field_validator("directory_path")
    @classmethod
    def _normalise_path(cls, value: Optional[str]) -> Optional[str]:
        text = (value or "").strip()
        return text or None


class ProblemImportResult(ProblemDetail):
    """``ProblemDetail`` plus what the pack importer actually did."""

    pack_name: str = ""
    pack_source: str = Field(default="", description="'dir' or 'zip'.")
    sample_case_count: int = 0
    random_case_count: int = 0
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Submissions
# --------------------------------------------------------------------------- #


class SubmissionCreate(BaseModel):
    """Body of ``POST /api/problems/{id}/submit``."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "language": "c",
                    "code": "#include <stdio.h>\nint main(void){int a,b;"
                    "if(scanf(\"%d %d\",&a,&b)!=2)return 1;"
                    "printf(\"%d\\n\",a+b);return 0;}\n",
                }
            ]
        }
    )

    language: LanguageLiteral = Field(..., description="'c' or 'cpp'.")
    code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)

    @field_validator("code")
    @classmethod
    def _reject_binary_garbage(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("code must not contain NUL bytes")
        return _lf(value) or ""


class CaseVerdict(BaseModel):
    """Verdict for one test case."""

    test_case_id: int
    is_sample: bool
    passed: bool
    status: CaseStatusLiteral
    execution_time_ms: float = 0.0
    exit_code: Optional[int] = None
    signal_name: Optional[str] = None
    truncated: bool = False
    presentation_warning: bool = Field(default=False)
    input: Optional[str] = None
    expected_output: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = Field(default=None)


class HiddenSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0


class RubricScores(BaseModel):
    """The 100-point teaching rubric, as measured for one submission."""

    compile_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_COMPILE_POINTS)
    sample_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_LOGIC_POINTS)
    random_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_RANDOM_POINTS)
    logic_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_LOGIC_POINTS)
    quality_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_QUALITY_POINTS)
    total: float = Field(default=0.0, ge=0.0, le=100.0)


class PatchProbeReport(BaseModel):
    """What probe_minimal_patch concluded about a submission."""

    needed: bool
    patch_success: bool
    probed_logic_score: float
    details: str
    baseline_status: str = ""
    applied_fixes: list[str] = Field(default_factory=list)
    attempted_fixes: list[str] = Field(default_factory=list)
    cases_tested: int = 0
    cases_passed: int = 0
    stopped_early: bool = False


class SubmissionResult(BaseModel):
    """Full grading report returned by ``POST /api/problems/{id}/submit``."""

    submission_id: Optional[int] = Field(default=None)
    problem_id: int
    student_id: int
    language: str
    status: SubmissionStatusLiteral
    score: float = Field(..., description="Passed cases as a percentage, 0-100.")
    passed_count: int
    total_count: int
    execution_time_ms: float
    compile_time_ms: float = 0.0
    compile_output: str = ""
    time_limit_sec: float
    memory_limit_mb: int
    trial_run: bool = False
    results: list[CaseVerdict] = Field(default_factory=list)
    hidden_summary: HiddenSummary = Field(default_factory=HiddenSummary)
    rubric: RubricScores = Field(default_factory=RubricScores)
    presentation_warning: bool = False
    wording_pass: bool = True
    wording_violations: list[str] = Field(default_factory=list)
    patch_probe: Optional[PatchProbeReport] = Field(default=None)


class SubmissionOut(BaseModel):
    """Row of ``GET /api/submissions``."""

    id: int
    problem_id: int
    problem_title: str
    student_id: int
    student_username: str
    student_name: str = ""
    language: str
    status: SubmissionStatusLiteral
    score: float
    passed_count: int
    total_count: int
    execution_time_ms: float
    created_at: UtcDatetime
    ai_feedback: Optional[str] = None
    ai_suggested_score: Optional[float] = None
    ai_model: Optional[str] = None
    ai_reviewed_at: Optional[UtcDatetime] = None
    teacher_feedback: Optional[str] = None
    teacher_score: Optional[float] = None
    reviewed_by_id: Optional[int] = None
    reviewed_at: Optional[UtcDatetime] = None
    rubric_total: float = 0.0


class SubmissionListResponse(BaseModel):
    total: int
    count: int
    submissions: list[SubmissionOut]


class SubmissionDetailOut(SubmissionOut):
    """One recorded attempt, including the submitted source for review."""

    code: str
    rubric: RubricScores = Field(default_factory=RubricScores)
    presentation_warning: bool = False
    wording_pass: bool = True
    wording_violations: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Plagiarism Screen
# --------------------------------------------------------------------------- #


class PlagiarismPair(BaseModel):
    """One suspicious pair, as measured by plagiarism screen."""

    student_a: str
    student_b: str
    submission_id_a: int
    submission_id_b: int
    language: str = ""
    combined_similarity: float = Field(ge=0.0, le=1.0)
    ast_similarity: float = Field(ge=0.0, le=1.0)
    token_similarity: float = Field(ge=0.0, le=1.0)
    ngram_jaccard: float = Field(ge=0.0, le=1.0)
    ref_similarity_a: Optional[float] = None
    ref_similarity_b: Optional[float] = None
    flagged: bool = False
    exemption: Optional[str] = None


class PlagiarismReport(BaseModel):
    """Result of one structural-similarity screen over a problem's submissions."""

    problem_id: int
    problem_title: str = ""
    problem_code: Optional[str] = None
    classroom_id: int
    classroom_name: str = ""
    threshold: float
    reference_available: bool = False
    languages: list[str] = Field(default_factory=list)
    submissions_compared: int = 0
    students_compared: int = 0
    pair_count: int = 0
    flagged_count: int = 0
    exempt_count: int = 0
    skipped_trivial: int = 0
    truncated: bool = False
    pairs: list[PlagiarismPair] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# AI Tutor & Teacher Review Schemas
# --------------------------------------------------------------------------- #


class AIStatusResponse(BaseModel):
    """Reachability of the configured vLLM tutor."""

    available: bool
    provider: str
    model: str
    base_url: str
    detail: str = ""


class AIAnalyzeRequest(BaseModel):
    """Ad-hoc review request."""

    code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    language: LanguageLiteral
    status: Optional[str] = "unknown"
    compile_output: Optional[str] = ""
    stderr: Optional[str] = ""
    problem_id: Optional[int] = Field(default=None, ge=1)


class AIAnalyzeResponse(BaseModel):
    """Structured review produced by ai_tutor.analyze_code."""

    summary: str
    error_analysis: str
    time_complexity: str
    space_complexity: str
    suggestions: list[str]
    suggested_score: float
    feedback_markdown: str
    model: str
    ai_powered: bool
    detail: str = ""


class SubmissionAIReviewResponse(SubmissionDetailOut):
    """A submission refreshed after an AI review was stored on it."""

    analysis: AIAnalyzeResponse


class TeacherReviewRequest(BaseModel):
    """Manual grading of one submission by a teacher/admin."""

    teacher_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    teacher_feedback: Optional[str] = Field(default=None, max_length=10000)


# --------------------------------------------------------------------------- #
# Exam Lockdown & Anti-Cheat Schemas
# --------------------------------------------------------------------------- #


class ExamSessionStartRequest(BaseModel):
    """Body of ``POST /api/exams/{problem_id}/start``."""

    agent_lock_token: Optional[str] = Field(
        default=None, description="Verification token from CodingPass Local Agent."
    )


class ExamHeartbeatRequest(BaseModel):
    """Body of ``POST /api/exams/{session_id}/heartbeat``.

    Every telemetry field is deliberately range-constrained: a tampered
    client must not be able to inflate counters with a single giant
    increment, nor smuggle an oversized blob through the violation label.
    """

    is_fullscreen: bool
    blur_increment: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Window-blur events observed since the previous beat (max 10).",
    )
    fullscreen_exited_increment: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Fullscreen-exit events since the previous beat (max 10). "
        "Legacy clients may omit it; the server then infers 1 from "
        "``is_fullscreen == False``.",
    )
    agent_locked: bool = Field(
        default=False,
        description="Local Agent reports its keyboard hook currently engaged.",
    )
    agent_blocked_count: int = Field(
        default=0,
        ge=0,
        le=1_000_000_000,
        description="Cumulative blocked key-combination count from the Local Agent hook.",
    )
    violation_event: Optional[str] = Field(
        default=None, max_length=64, description="Short violation label, e.g. 'ALT_TAB_SWITCH'."
    )


class ExamHeartbeatResponse(BaseModel):
    """Response to ``POST /api/exams/{session_id}/heartbeat``."""

    status: str
    blur_count: int
    max_blur_count: int
    fullscreen_exited_count: int
    agent_lock_verified: bool
    warning: Optional[str] = None


class ExamSessionOut(BaseModel):
    """Detailed exam session state."""

    id: int
    problem_id: int
    problem_title: str
    student_id: int
    student_username: str
    student_name: str
    start_time: UtcDatetime
    end_time: Optional[UtcDatetime] = None
    blur_count: int
    fullscreen_exited_count: int
    agent_blocked_count: int = 0
    agent_lock_verified: bool
    last_heartbeat: UtcDatetime
    status: str
    violations: list[dict] = Field(default_factory=list)


class ExamMonitorResponse(BaseModel):
    """Classroom or problem live proctoring overview."""

    problem_id: int
    problem_title: str
    is_exam: bool
    exam_lock_required: bool
    max_blur_count: int
    active_students: int
    sessions: list[ExamSessionOut]
