"""FastAPI application for the CodingPass C/C++ judge, classroom and exam server.

Endpoints
---------
**Service**
``GET  /api/health``       liveness probe plus the detected toolchain versions.
``GET  /api/config``       public sandbox configuration.
``POST /api/run``          compile + execute a snippet and return judge output
                           (the ad-hoc playground used by the student editor).

**Accounts** (see :mod:`auth`)
``POST /api/auth/register`` create an account (public: students only).
``POST /api/auth/login``    exchange credentials for a JWT bearer token.
``POST /api/auth/token``    the same, as an OAuth2 password form (Swagger UI).
``GET  /api/auth/me``       the authenticated account.

**Classrooms**
``GET  /api/classrooms``             classes visible to the caller.
``POST /api/classrooms``             teacher creates a class (invite code).
``POST /api/classrooms/join``        student joins with an invite code.
``GET  /api/classrooms/{id}``        class detail with the enrolled roster.

**Problem bank**
``GET  /api/problems``               problems visible to the caller.
``POST /api/problems``               teacher creates a problem with test cases.
``GET  /api/problems/{id}``          detail + starter code + *sample* cases only.
``POST /api/problems/{id}/testcases`` teacher appends one test case.
``POST /api/problems/{id}/submit``   judge a submission against **all** cases.
``GET  /api/submissions``            own submissions, or a teacher's class view.

Authorisation is a JWT in ``Authorization: Bearer <token>``. Every ``/api/auth/me``,
``/api/problems/{id}/submit`` and classroom/problem write route answers ``401``
when the token is missing or invalid and ``403`` when the role is not allowed.
A problem that exists but is not on a student's syllabus is reported as ``403``;
a problem that does not exist is ``404``.

Run it from ``backend/`` with::

    uvicorn main:app --reload --host 127.0.0.1 --port 8010

or ``python main.py``. Interactive docs live at ``/docs``. The SQLite database
(``codingpass.db``) and its demo dataset are created automatically at startup;
see :mod:`database` for the seeded accounts.

Configuration (environment)
---------------------------
``CORS_ALLOW_ORIGINS``  comma separated origins, or ``*`` to allow everyone.
``MAX_CONCURRENT_RUNS`` how many submissions may execute at the same time.
``DATABASE_URL``        SQLAlchemy URL (default ``sqlite:///backend/codingpass.db``).
``JWT_SECRET_KEY``      HS256 signing key - see :mod:`auth` for the fallback.
See ``compiler.py`` for the sandbox limits (timeout, memory, output size).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool

try:  # package-style launch: `uvicorn backend.main:app`
    from .compiler import (
        DEFAULT_TIMEOUT_SECONDS,
        MEMORY_LIMIT_BYTES,
        Status,
        RunResult,
        limits_summary,
        run_code,
        supported_languages,
        toolchain_versions,
    )
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from compiler import (  # type: ignore[no-redef]
        DEFAULT_TIMEOUT_SECONDS,
        MEMORY_LIMIT_BYTES,
        Status,
        RunResult,
        limits_summary,
        run_code,
        supported_languages,
        toolchain_versions,
    )

try:  # package-style launch: `uvicorn backend.main:app`
    from .auth import (
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        require_roles,
        verify_password_for_login,
    )
    from .database import init_db
    from .models import (
        Classroom,
        ClassroomMembership,
        Difficulty,
        DifficultyLiteral,
        Problem,
        Role,
        RoleLiteral,
        Submission,
        SubmissionStatus,
        SubmissionStatusLiteral,
        TestCase,
        User,
    )
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from auth import (  # type: ignore[no-redef]
        create_access_token,
        get_current_user,
        get_db,
        get_optional_user,
        hash_password,
        require_roles,
        verify_password_for_login,
    )
    from database import init_db  # type: ignore[no-redef]
    from models import (  # type: ignore[no-redef]
        Classroom,
        ClassroomMembership,
        Difficulty,
        DifficultyLiteral,
        Problem,
        Role,
        RoleLiteral,
        Submission,
        SubmissionStatus,
        SubmissionStatusLiteral,
        TestCase,
        User,
    )

logging.basicConfig(
    level=os.environ.get("CODE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("codingpass.api")

#: Largest accepted submission, in characters.
MAX_SOURCE_CHARS = 200_000
#: Largest accepted stdin payload, in characters.
MAX_STDIN_CHARS = 200_000
#: Largest accepted ``expected_output`` payload, in characters.
MAX_EXPECTED_CHARS = 100_000
#: Largest teacher-authored problem statement, in characters.
MAX_DESCRIPTION_CHARS = 20_000
#: How many submissions may be compiled/executed simultaneously.
MAX_CONCURRENT_RUNS = max(1, int(os.environ.get("MAX_CONCURRENT_RUNS", "4")))
#: Test cases per problem. A submission is judged case by case, so this also
#: bounds the worst-case latency of ``POST /api/problems/{id}/submit``
#: (``test_cases x (compile + time_limit)``).
MAX_TEST_CASES = 50
#: Teacher-selectable bounds for the per-problem limits.
MIN_TIME_LIMIT_SEC = 0.1
MAX_TIME_LIMIT_SEC = 20.0
MIN_MEMORY_LIMIT_MB = 16
MAX_MEMORY_LIMIT_MB = 4096
#: Characters of stderr / compiler output echoed back inside one case verdict.
VERDICT_DETAIL_CHARS = 2_000
#: Rows returned by a paginated list endpoint when the client says nothing.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
#: Invite-code alphabet used when the server invents a code: uppercase letters
#: without ``I``/``O`` and digits without ``0``/``1`` (whiteboard-friendly).
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 6

# Patterns are unanchored on purpose: every use site calls ``fullmatch``, which
# cannot be fooled by the trailing-newline quirk of ``$``.
USERNAME_RE = re.compile(r"[a-z][a-z0-9._-]{2,31}")
EMAIL_RE = re.compile(r"[^@\s;]+@[^@\s;]+\.[^@\s;]+")
INVITE_CODE_RE = re.compile(r"[A-Z0-9_-]{3,16}")

StatusLiteral = Literal[
    "success", "compile_error", "time_limit_exceeded", "runtime_error", "internal_error"
]
LanguageLiteral = Literal["c", "cpp"]
#: Verdict vocabulary of a single test case inside a graded submission.
CaseStatusLiteral = Literal[
    "accepted", "wrong_answer", "time_limit_exceeded", "runtime_error", "compile_error"
]


# --------------------------------------------------------------------------- #
# Schemas
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


#: Generic scaffolds handed to a student who creates a problem without starter
#: code: valid, compiling, deliberately not yet correct.
DEFAULT_STARTER_C = """\
#include <stdio.h>

int main(void) {
    /* TODO: read the input, compute the answer, print it. */
    printf("\\n");
    return 0;
}
"""

DEFAULT_STARTER_CPP = """\
#include <iostream>

int main() {
    // TODO: read the input, compute the answer, print it.
    std::cout << std::endl;
    return 0;
}
"""


# --------------------------------------------------------------------------- #
# Schemas: accounts
# --------------------------------------------------------------------------- #


def _utc_iso(value: datetime) -> str:
    """Render a stored naive-UTC timestamp as an explicit ISO-8601 UTC string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


#: Timestamp field that always serialises with a ``+00:00`` offset, so the
#: browser can format it in the viewer's own timezone.
UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, when_used="json")]


def _lf(value: Optional[str]) -> Optional[str]:
    """Normalise CRLF/CR line endings to LF (Windows editors, browser uploads)."""
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


class UserOut(BaseModel):
    """Public view of an account; the password digest is never serialised."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    full_name: str
    role: RoleLiteral
    created_at: UtcDatetime


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
# Schemas: classrooms
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
    problems: list["ProblemSummary"] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Schemas: problems and test cases
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
        description="Advertised budget; the sandbox ceiling is CODE_MEMORY_LIMIT_MB.",
    )
    classroom_id: Optional[int] = Field(
        default=None,
        description="Omit to keep the problem in your private bank.",
    )
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


# ``ClassroomDetail`` forward-references this model, so complete the schema now.
ClassroomDetail.model_rebuild()


class ProblemDetail(ProblemSummary):
    """``GET /api/problems/{id}`` payload.

    ``test_cases`` contains the **sample** cases only; the number of graded cases
    is exposed as ``hidden_count`` so nobody can probe the hidden data.
    """

    description: str = ""
    starter_code_c: str = ""
    starter_code_cpp: str = ""
    test_cases: list[TestCasePublic] = Field(default_factory=list)


class ProblemListResponse(BaseModel):
    total: int
    count: int
    problems: list[ProblemSummary]


# --------------------------------------------------------------------------- #
# Schemas: submissions
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
    """Verdict for one test case.

    ``input`` / ``expected_output`` / ``stdout`` are ``None`` for hidden cases
    when the caller is not allowed to see them (a student, always).
    """

    test_case_id: int
    is_sample: bool
    passed: bool
    status: CaseStatusLiteral
    execution_time_ms: float = 0.0
    exit_code: Optional[int] = None
    signal_name: Optional[str] = None
    truncated: bool = False
    input: Optional[str] = None
    expected_output: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = Field(
        default=None, description="Trimmed diagnostic (empty on a passing case)."
    )


class HiddenSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0


class SubmissionResult(BaseModel):
    """Full grading report returned by ``POST /api/problems/{id}/submit``."""

    submission_id: Optional[int] = Field(
        default=None, description="``null`` for a teacher/admin trial run."
    )
    problem_id: int
    student_id: int
    language: str
    status: SubmissionStatusLiteral
    score: float = Field(..., description="Passed cases as a percentage, 0-100.")
    passed_count: int
    total_count: int
    execution_time_ms: float = Field(..., description="Slowest single case.")
    compile_time_ms: float = Field(
        default=0.0, description="Slowest single compile of the submission, in milliseconds."
    )
    compile_output: str = Field(
        default="", description="Compiler diagnostics, including warnings on success."
    )
    time_limit_sec: float
    memory_limit_mb: int
    trial_run: bool = Field(
        default=False, description="True when a teacher/admin ran the case without grading."
    )
    results: list[CaseVerdict] = Field(default_factory=list)
    hidden_summary: HiddenSummary = Field(default_factory=HiddenSummary)


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


class SubmissionListResponse(BaseModel):
    total: int
    count: int
    submissions: list[SubmissionOut]


class SubmissionDetailOut(SubmissionOut):
    """One recorded attempt, including the submitted source for review."""

    code: str


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
    if raw == "*":
        return ["*"]
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def _execution_slots(request: Request) -> asyncio.Semaphore:
    """Return the process-wide judge semaphore.

    Created in :func:`lifespan`; the fallback keeps the routes working when the
    app is embedded in another ASGI server that skips lifespan handlers.
    """
    slots: Optional[asyncio.Semaphore] = getattr(request.app.state, "run_slots", None)
    if slots is None:
        slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        request.app.state.run_slots = slots
    return slots


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the execution semaphore and the database for the worker's lifetime."""
    app.state.run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    logger.info(
        "judge ready: languages=%s timeout=%.1fs memory=%dMB concurrency=%d",
        supported_languages(),
        DEFAULT_TIMEOUT_SECONDS,
        MEMORY_LIMIT_BYTES // (1024 * 1024),
        MAX_CONCURRENT_RUNS,
    )
    try:
        # Creates the schema and the demo dataset; raises only for a real
        # connectivity problem, which must not stop the judge routes.
        await run_in_threadpool(init_db)
        app.state.db_ready = True
    except Exception:  # pragma: no cover - exercised by operators, not tests
        app.state.db_ready = False
        logger.exception(
            "database initialisation failed - /api/health and /api/run still "
            "work, but accounts, classrooms and grading are unavailable"
        )
    try:
        yield
    finally:
        app.state.run_slots = None


app = FastAPI(
    title="CodingPass Judge",
    description=(
        "Multi-user C / C++ teaching platform: accounts with admin / teacher / "
        "student roles, classrooms with invite codes, a problem bank with hidden "
        "test cases, and a resource-limited execution sandbox."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    # The Vite dev server runs on :5173; any localhost port is accepted so that
    # preview servers work too. Set CORS_ALLOW_ORIGINS='*' to open it fully.
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=origins != ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    # 'Authorization' carries the JWT from the Vite dev server on :5173; without
    # it the browser blocks every authenticated cross-origin request.
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report service liveness and the compiler toolchain that was detected."""
    versions = await run_in_threadpool(toolchain_versions)
    gcc_version = versions.get("gcc", "gcc not available")
    gpp_version = versions.get("g++", "g++ not available")
    degraded = "not available" in gcc_version or "not available" in gpp_version
    return HealthResponse(
        status="degraded" if degraded else "ok",
        gcc_version=gcc_version,
        gpp_version=gpp_version,
        languages=supported_languages(),
        limits=limits_summary(),
    )


@app.post("/api/run", response_model=RunResponse)
async def run_submission(request: Request, payload: RunRequest) -> RunResponse:
    """Compile the submission and run it against ``stdin``.

    Returns HTTP 200 for every judged outcome; inspect ``status`` for
    ``success`` / ``compile_error`` / ``time_limit_exceeded`` / ``runtime_error``.
    Malformed requests (unknown language, missing ``code``, oversized payload)
    still produce a normal 422 validation error.
    """
    # Created in lifespan; the fallback keeps the route working when the app is
    # embedded or tested without running lifespan handlers.
    async with _execution_slots(request):
        result: RunResult = await run_in_threadpool(
            run_code,
            payload.code,
            payload.language,
            payload.stdin or "",
            DEFAULT_TIMEOUT_SECONDS,
        )

    logger.info(
        "judged %s submission: status=%s exit=%s time=%.1fms",
        result.language or payload.language,
        result.status,
        result.exit_code,
        result.execution_time_ms,
    )
    return RunResponse(**result.to_dict())


@app.get("/api/config")
async def config() -> dict:
    """Public sandbox configuration, so the UI can label its timings/limits."""
    return {
        "languages": supported_languages(),
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        "memory_limit_mb": MEMORY_LIMIT_BYTES // (1024 * 1024),
        "max_output_bytes": limits_summary()["max_output_bytes"],
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "max_source_chars": MAX_SOURCE_CHARS,
        "max_test_cases": MAX_TEST_CASES,
        "time_limit_bounds": [MIN_TIME_LIMIT_SEC, MAX_TIME_LIMIT_SEC],
        "memory_limit_bounds": [MIN_MEMORY_LIMIT_MB, MAX_MEMORY_LIMIT_MB],
        "roles": list(Role.ALL),
        "difficulties": list(Difficulty.ALL),
    }


# --------------------------------------------------------------------------- #
# Grading helpers
# --------------------------------------------------------------------------- #

#: Route guards shared by the endpoints below. ``require_roles`` validates the
#: role names at import time, so a typo stops the server instead of opening a hole.
StudentOnly = require_roles(Role.STUDENT)
TeacherOnly = require_roles(Role.TEACHER, Role.ADMIN)
AdminOnly = require_roles(Role.ADMIN)


def _normalize_output(text: Optional[str]) -> str:
    r"""Canonical form used to compare program output with the expected answer.

    Ignored: carriage returns, trailing spaces/tabs on each line, and leading or
    trailing blank lines. Still significant: line order, internal spacing,
    spelling and capitalisation. ``"5"``, ``"5 \n\n"`` and ``"5\n"`` therefore all
    pass against an expected ``"5\n"``, while ``" 5"`` and ``"five"`` do not.
    """
    if not text:
        return ""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalised.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _clip(text: Optional[str], limit: int = VERDICT_DETAIL_CHARS) -> str:
    """Bound a diagnostic so one chatty test case cannot dominate the response."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... clipped {len(text) - limit} characters]"


@dataclass
class CaseOutcome:
    """Internal per-test-case result before visibility filtering."""

    test_case_id: int
    is_sample: bool
    status: str
    passed: bool
    execution_time_ms: float
    exit_code: Optional[int]
    signal_name: Optional[str]
    truncated: bool
    stdin: str
    expected_output: str
    stdout: str
    stderr: str


@dataclass
class JudgeReport:
    """Aggregate of one submission run over every test case of a problem."""

    outcomes: list[CaseOutcome] = field(default_factory=list)
    compile_output: str = ""
    compile_time_ms: float = 0.0
    compile_failed: bool = False
    judge_error: str = ""

    def passed_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.passed)

    def slowest_ms(self) -> float:
        return max((outcome.execution_time_ms for outcome in self.outcomes), default=0.0)

    def aggregate_status(self, total_count: int) -> str:
        """Collapse the per-case verdicts into the stored submission verdict.

        A case that produced the wrong answer outranks nothing: when several
        cases fail for different reasons the most instructive one wins, in the
        order *did not compile* \u2192 *ran out of time* \u2192 *crashed* \u2192 *wrong output*.
        """
        if self.compile_failed:
            return SubmissionStatus.COMPILE_ERROR
        if total_count > 0 and self.passed_count() >= total_count:
            return SubmissionStatus.ACCEPTED
        seen = {outcome.status for outcome in self.outcomes}
        if Status.TIME_LIMIT_EXCEEDED in seen:
            return SubmissionStatus.TIME_LIMIT_EXCEEDED
        if Status.RUNTIME_ERROR in seen:
            return SubmissionStatus.RUNTIME_ERROR
        return SubmissionStatus.WRONG_ANSWER


def _judge_all_cases(
    code: str,
    language: str,
    cases: list[TestCase],
    timeout_seconds: float,
) -> JudgeReport:
    """Compile and execute ``code`` once per test case and score the outputs.

    Runs in a worker thread. A compile failure short-circuits the loop (every
    case would fail identically); a sandbox ``internal_error`` aborts the whole
    run because the environment, not the submission, is broken. Timing out or
    crashing on one case does **not** stop the remaining cases: a partially
    correct program still earns credit for the cases it survived.

    Each call re-compiles, because :func:`compiler.run_code` owns the whole
    compile-and-run lifecycle inside one isolated temporary directory. Worst-case
    latency is therefore ``len(cases) * (compile + timeout_seconds)``; that is
    what ``MAX_TEST_CASES`` bounds.
    """
    report = JudgeReport()
    for case_row in cases:
        result: RunResult = run_code(code, language, case_row.input, timeout_seconds)

        if result.status == Status.COMPILE_ERROR:
            report.compile_failed = True
            report.compile_output = result.compile_output
            report.compile_time_ms = result.compile_time_ms
            return report

        if result.status == Status.INTERNAL_ERROR:
            report.judge_error = _clip(
                result.stderr or "the sandbox could not execute this submission"
            )
            return report

        if not report.compile_output and result.compile_output:
            # Keep the compiler's warnings: they are useful feedback even when
            # the program compiles and runs.
            report.compile_output = result.compile_output
        # Every case compiles the same source, so the timings are comparable;
        # report the slowest one.
        report.compile_time_ms = max(report.compile_time_ms, result.compile_time_ms)

        expected = case_row.expected_output or ""
        passed = result.status == Status.SUCCESS and _normalize_output(
            result.stdout
        ) == _normalize_output(expected)
        if result.status == Status.SUCCESS:
            case_status = SubmissionStatus.ACCEPTED if passed else SubmissionStatus.WRONG_ANSWER
        else:
            # ``time_limit_exceeded`` / ``runtime_error`` carry over unchanged.
            case_status = result.status

        report.outcomes.append(
            CaseOutcome(
                test_case_id=case_row.id,
                is_sample=bool(case_row.is_sample),
                status=case_status,
                passed=passed,
                execution_time_ms=result.execution_time_ms,
                exit_code=result.exit_code,
                signal_name=result.signal_name,
                truncated=bool(result.truncated),
                stdin=case_row.input or "",
                expected_output=expected,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
    return report


def _case_verdict(outcome: CaseOutcome, reveal_hidden: bool) -> CaseVerdict:
    """Render one outcome, redacting hidden data for callers not entitled to it.

    A student sees input, expected output and actual output **only** for sample
    cases. On a hidden case they get the verdict and timing: anything more would
    turn the judge into an oracle for reverse-engineering the test data.
    """
    visible = outcome.is_sample or reveal_hidden
    return CaseVerdict(
        test_case_id=outcome.test_case_id,
        is_sample=outcome.is_sample,
        passed=outcome.passed,
        status=outcome.status,  # type: ignore[arg-type]
        execution_time_ms=outcome.execution_time_ms,
        exit_code=outcome.exit_code,
        signal_name=outcome.signal_name,
        truncated=outcome.truncated,
        input=outcome.stdin if visible else None,
        expected_output=outcome.expected_output if visible else None,
        stdout=outcome.stdout if visible else None,
        stderr=(_clip(outcome.stderr) if visible else "") or None,
    )


# --------------------------------------------------------------------------- #
# Visibility / authorisation helpers
# --------------------------------------------------------------------------- #


def _student_classroom_ids(db: Session, student: User) -> list[int]:
    """Classroom ids the student is enrolled in."""
    rows = (
        db.query(ClassroomMembership.classroom_id)
        .filter(ClassroomMembership.student_id == student.id)
        .all()
    )
    return [int(row[0]) for row in rows]


def _teacher_classroom_ids(db: Session, teacher: User) -> list[int]:
    rows = db.query(Classroom.id).filter(Classroom.teacher_id == teacher.id).all()
    return [int(row[0]) for row in rows]


def _problem_conditions(db: Session, user: User) -> list:
    """SQL restrictions that express "what this user may see" for problems."""
    if user.is_admin:
        return []
    if user.is_teacher:
        owned = _teacher_classroom_ids(db, user)
        allowed = [Problem.teacher_id == user.id]
        if owned:
            allowed.append(Problem.classroom_id.in_(owned))
        return [or_(*allowed)]
    return [Problem.classroom_id.in_(_student_classroom_ids(db, user))]


def _can_manage_problem(problem: Problem, user: User) -> bool:
    """True when ``user`` may edit the problem or inspect its hidden cases."""
    return user.is_admin or problem.teacher_id == user.id


def _assert_problem_visible(problem: Problem, user: User, db: Session) -> None:
    """Raise ``403`` when ``user`` may not open ``problem``."""
    if user.is_admin or problem.teacher_id == user.id:
        return
    if user.is_teacher:
        if problem.classroom_id and problem.classroom_id in _teacher_classroom_ids(db, user):
            return
        logger.info(
            "user %s (teacher) has no rights on problem %s", user.id, problem.id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This problem belongs to another teacher's course.",
        )
    if problem.classroom_id and problem.classroom_id in _student_classroom_ids(db, user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "This problem is not part of a class you joined"
            + (" yet" if not problem.classroom_id else "")
            + "."
        ),
    )


def _assert_classroom_visible(classroom: Classroom, user: User) -> None:
    """Raise ``403`` when ``user`` neither teaches, owns nor attends ``classroom``."""
    if user.is_admin or classroom.teacher_id == user.id:
        return
    if classroom.has_student(user.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not a member of this class.",
    )


def _load_problem(db: Session, problem_id: int) -> Problem:
    """Fetch a problem with its teacher and test cases, or raise ``404``."""
    problem = (
        db.query(Problem)
        .options(joinedload(Problem.teacher), selectinload(Problem.test_cases))
        .filter(Problem.id == problem_id)
        .one_or_none()
    )
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found."
        )
    return problem


def _load_classroom(db: Session, classroom_id: int) -> Classroom:
    """Fetch a classroom with its roster loaded, or raise ``404``."""
    classroom = (
        db.query(Classroom)
        .options(joinedload(Classroom.teacher), selectinload(Classroom.memberships))
        .filter(Classroom.id == classroom_id)
        .one_or_none()
    )
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Class not found."
        )
    return classroom


def _generate_class_code(db: Session) -> str:
    """Invent an unused invite code, avoiding visually ambiguous characters."""
    for _ in range(25):
        candidate = "".join(
            secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH)
        )
        if db.query(Classroom.id).filter(Classroom.code == candidate).first() is None:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not allocate a unique class code; supply one explicitly.",
    )


# --------------------------------------------------------------------------- #
# Response builders
# --------------------------------------------------------------------------- #


def _problem_counts(db: Session, problem_ids: list[int]) -> dict[int, tuple[int, int]]:
    """Map problem id -> ``(sample_count, hidden_count)`` in one query."""
    if not problem_ids:
        return {}
    rows = (
        db.query(
            TestCase.problem_id,
            func.sum(case((TestCase.is_sample.is_(True), 1), else_=0)),
            func.sum(case((TestCase.is_sample.is_(False), 1), else_=0)),
        )
        .filter(TestCase.problem_id.in_(problem_ids))
        .group_by(TestCase.problem_id)
        .all()
    )
    return {int(pid): (int(samples or 0), int(hidden or 0)) for pid, samples, hidden in rows}


def _classroom_counts(
    db: Session, classroom_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Return ``(student_counts, problem_counts)`` keyed by classroom id."""
    if not classroom_ids:
        return {}, {}
    student_rows = (
        db.query(ClassroomMembership.classroom_id, func.count(ClassroomMembership.id))
        .filter(ClassroomMembership.classroom_id.in_(classroom_ids))
        .group_by(ClassroomMembership.classroom_id)
        .all()
    )
    problem_rows = (
        db.query(Problem.classroom_id, func.count(Problem.id))
        .filter(Problem.classroom_id.in_(classroom_ids))
        .group_by(Problem.classroom_id)
        .all()
    )
    return (
        {int(cid): int(count) for cid, count in student_rows},
        {int(cid): int(count) for cid, count in problem_rows},
    )


def _problem_summary(problem: Problem, counts: dict[int, tuple[int, int]]) -> ProblemSummary:
    samples, hidden = counts.get(problem.id, (0, 0))
    return ProblemSummary(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,  # type: ignore[arg-type]
        time_limit_sec=problem.time_limit_sec,
        memory_limit_mb=problem.memory_limit_mb,
        classroom_id=problem.classroom_id,
        teacher_id=problem.teacher_id,
        teacher_name=problem.teacher.display_name if problem.teacher else "",
        created_at=problem.created_at,
        sample_count=samples,
        hidden_count=hidden,
    )


def _problem_detail(problem: Problem, reveal_hidden: bool) -> ProblemDetail:
    """Full problem payload.

    Students receive the sample cases in ``test_cases`` and merely learn that
    ``hidden_count`` further cases exist. The problem's own teacher (or an admin)
    receives every case, flagged by ``is_sample``.
    """
    visible_cases = problem.test_cases if reveal_hidden else problem.sample_test_cases
    return ProblemDetail(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,  # type: ignore[arg-type]
        time_limit_sec=problem.time_limit_sec,
        memory_limit_mb=problem.memory_limit_mb,
        classroom_id=problem.classroom_id,
        teacher_id=problem.teacher_id,
        teacher_name=problem.teacher.display_name if problem.teacher else "",
        created_at=problem.created_at,
        sample_count=len(problem.sample_test_cases),
        hidden_count=len(problem.hidden_test_cases),
        description=problem.description,
        starter_code_c=problem.starter_code_c,
        starter_code_cpp=problem.starter_code_cpp,
        test_cases=[
            TestCasePublic(
                id=case_row.id,
                input=case_row.input,
                expected_output=case_row.expected_output,
                is_sample=case_row.is_sample,
            )
            for case_row in visible_cases
        ],
    )


def _classroom_out(
    classroom: Classroom,
    student_counts: dict[int, int],
    problem_counts: dict[int, int],
) -> ClassroomOut:
    return ClassroomOut(
        id=classroom.id,
        name=classroom.name,
        code=classroom.code,
        description=classroom.description,
        teacher_id=classroom.teacher_id,
        teacher_name=classroom.teacher.display_name if classroom.teacher else "",
        created_at=classroom.created_at,
        student_count=student_counts.get(classroom.id, 0),
        problem_count=problem_counts.get(classroom.id, 0),
    )


def _classroom_detail(db: Session, classroom: Classroom) -> ClassroomDetail:
    """Classroom summary plus roster and the problem set published to it."""
    memberships = (
        db.query(ClassroomMembership)
        .options(joinedload(ClassroomMembership.student))
        .filter(ClassroomMembership.classroom_id == classroom.id)
        .order_by(ClassroomMembership.joined_at, ClassroomMembership.id)
        .all()
    )
    problems = (
        db.query(Problem)
        .options(joinedload(Problem.teacher))
        .filter(Problem.classroom_id == classroom.id)
        .order_by(Problem.created_at, Problem.id)
        .all()
    )
    counts = _problem_counts(db, [problem.id for problem in problems])
    student_counts, problem_counts = _classroom_counts(db, [classroom.id])
    base = _classroom_out(classroom, student_counts, problem_counts)
    return ClassroomDetail(
        **base.model_dump(),
        teacher_username=classroom.teacher.username if classroom.teacher else "",
        students=[
            ClassroomMemberOut(
                id=membership.student.id,
                username=membership.student.username,
                full_name=membership.student.full_name,
                email=membership.student.email,
                joined_at=membership.joined_at,
            )
            for membership in memberships
        ],
        problems=[_problem_summary(problem, counts) for problem in problems],
    )


# --------------------------------------------------------------------------- #
# Authentication endpoints
# --------------------------------------------------------------------------- #


@app.post(
    "/api/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    requester: Optional[User] = Depends(get_optional_user),
) -> UserOut:
    """Sign up an account.

    Public registration always produces a ``student``. Passing ``role`` as
    ``teacher`` or ``admin`` requires the ``Authorization`` header of a signed-in
    admin, so staff accounts cannot be self-assigned.
    """
    if payload.role != Role.STUDENT and (requester is None or not requester.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a signed-in admin can create teacher or admin accounts.",
        )

    if db.query(User.id).filter(User.username == payload.username).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That username is already taken."
        )
    if db.query(User.id).filter(User.email == payload.email).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered.",
        )

    # bcrypt at the default cost burns CPU for a fifth of a second: keep it off
    # the event loop so concurrent judges are not stalled.
    digest = await run_in_threadpool(hash_password, payload.password)
    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=digest,
        role=payload.role,
        full_name=payload.full_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a simultaneous sign-up with the same key.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username or email was taken while registering; please retry.",
        ) from None
    db.refresh(user)
    logger.info("registered %s account '%s' (id=%s)", user.role, user.username, user.id)
    return UserOut.model_validate(user)


@app.post(
    "/api/auth/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a JWT",
)
async def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Log in with a username **or** an email address and a password.

    Always answers ``401`` with the same wording, whether the account is unknown
    or the password is wrong, and always performs exactly one bcrypt comparison,
    so the endpoint is neither an enumeration oracle nor a timing oracle.
    """
    identifier = payload.username
    user = db.query(User).filter(func.lower(User.username) == identifier).one_or_none()
    if user is None:
        user = db.query(User).filter(func.lower(User.email) == identifier).one_or_none()

    password_ok = await run_in_threadpool(
        verify_password_for_login, payload.password, user.hashed_password if user else None
    )
    if user is None or not password_ok:
        logger.info("failed login attempt for %r", identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user)
    logger.info("issued a token to %s (role=%s)", user.username, user.role)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


@app.post(
    "/api/auth/token",
    response_model=TokenResponse,
    summary="Log in with an OAuth2 password form (Swagger UI)",
)
async def login_form(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Form-encoded twin of ``/api/auth/login`` used by the ``Authorize`` button.

    ``username`` accepts an email address too; the optional ``scope`` field is
    ignored because role-based access control, not scopes, guards the routes.
    """
    identifier = (form_data.username or "").strip().lower()
    user = db.query(User).filter(func.lower(User.username) == identifier).one_or_none()
    if user is None:
        user = db.query(User).filter(func.lower(User.email) == identifier).one_or_none()

    password_ok = await run_in_threadpool(
        verify_password_for_login, form_data.password, user.hashed_password if user else None
    )
    if user is None or not password_ok:
        logger.info("failed form login attempt for %r", identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


@app.get("/api/auth/me", response_model=UserOut, summary="Current account")
async def read_me(user: User = Depends(get_current_user)) -> UserOut:
    """Return the account the presented token belongs to."""
    return UserOut.model_validate(user)


# --------------------------------------------------------------------------- #
# Classroom endpoints
# --------------------------------------------------------------------------- #


@app.get("/api/classrooms", response_model=list[ClassroomOut], summary="List my classes")
async def list_classrooms(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ClassroomOut]:
    """Classes visible to the caller.

    * admin - every class on the instance.
    * teacher - the classes they own.
    * student - the classes they have joined.
    """
    query = db.query(Classroom).options(joinedload(Classroom.teacher))
    if user.is_admin:
        statement = query
    elif user.is_teacher:
        statement = query.filter(Classroom.teacher_id == user.id)
    else:
        statement = query.filter(Classroom.id.in_(_student_classroom_ids(db, user)))

    classrooms = statement.order_by(Classroom.created_at.desc(), Classroom.id.desc()).all()
    student_counts, problem_counts = _classroom_counts(db, [row.id for row in classrooms])
    return [
        _classroom_out(classroom, student_counts, problem_counts) for classroom in classrooms
    ]


@app.post(
    "/api/classrooms",
    response_model=ClassroomOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a class",
)
async def create_classroom(
    payload: ClassroomCreate,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ClassroomOut:
    """Create a class and its invite code.

    The code is generated when omitted (6 unambiguous characters from
    ``ABCDEFGHJKLMNPQRSTUVWXYZ23456789``). ``teacher_id`` is an admin-only
    override that hands the class to somebody else.
    """
    owner_id = teacher.id
    if payload.teacher_id is not None:
        if not teacher.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an admin can assign a class to another teacher.",
            )
        assignee = db.get(User, payload.teacher_id)
        if assignee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="That teacher does not exist."
            )
        if assignee.role == Role.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{assignee.username} is a student and cannot own a class.",
            )
        owner_id = assignee.id

    code = payload.code or _generate_class_code(db)
    if db.query(Classroom.id).filter(Classroom.code == code).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The invite code {code} is already in use.",
        )

    classroom = Classroom(
        name=payload.name,
        code=code,
        description=payload.description,
        teacher_id=owner_id,
    )
    db.add(classroom)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The invite code {code} was claimed first; please pick another.",
        ) from None
    db.refresh(classroom)
    db.refresh(classroom, attribute_names=["teacher"])
    logger.info(
        "teacher id=%s created class %r (%s)", teacher.id, classroom.name, classroom.code
    )
    return _classroom_out(classroom, {}, {})


@app.post(
    "/api/classrooms/join",
    response_model=ClassroomDetail,
    summary="Join a class with an invite code",
)
async def join_classroom(
    payload: ClassroomJoinRequest,
    db: Session = Depends(get_db),
    student: User = Depends(StudentOnly),
) -> ClassroomDetail:
    """Enrol the calling student in the class that owns ``code``."""
    classroom = (
        db.query(Classroom)
        .options(joinedload(Classroom.teacher), selectinload(Classroom.memberships))
        .filter(Classroom.code == payload.code)
        .one_or_none()
    )
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No class uses that invite code.",
        )
    if classroom.has_student(student.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already enrolled in {classroom.name}.",
        )

    db.add(ClassroomMembership(classroom_id=classroom.id, student_id=student.id))
    try:
        db.commit()
    except IntegrityError:
        # Two tabs joining at the same instant: the unique pair constraint wins.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already enrolled in {classroom.name}.",
        ) from None
    logger.info("student id=%s joined %s", student.id, classroom.code)
    return _classroom_detail(db, classroom)


@app.get(
    "/api/classrooms/{classroom_id}",
    response_model=ClassroomDetail,
    summary="Class detail with roster",
)
async def read_classroom(
    classroom_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ClassroomDetail:
    """Class details, the enrolled roster and the problems published to it.

    Visible to the owning teacher, an enrolled student and any admin.
    """
    classroom = _load_classroom(db, classroom_id)
    _assert_classroom_visible(classroom, user)
    return _classroom_detail(db, classroom)


# --------------------------------------------------------------------------- #
# Problem bank endpoints
# --------------------------------------------------------------------------- #


@app.get("/api/problems", response_model=ProblemListResponse, summary="List problems")
async def list_problems(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    classroom_id: Optional[int] = Query(default=None, ge=1, description="Filter by class."),
    difficulty: Optional[DifficultyLiteral] = Query(default=None, description="Filter by difficulty."),
    mine: bool = Query(default=False, description="Only problems created by me (teachers)."),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> ProblemListResponse:
    """Problems the caller is entitled to see, newest first.

    A student only ever sees problems attached to a class they joined, so a
    problem sitting in a teacher's private bank (``classroom_id = null``) is
    invisible to them.
    """
    conditions = _problem_conditions(db, user)
    if classroom_id is not None:
        conditions.append(Problem.classroom_id == classroom_id)
    if difficulty is not None:
        conditions.append(Problem.difficulty == difficulty)
    if mine:
        conditions.append(Problem.teacher_id == user.id)

    total = db.query(func.count(Problem.id)).filter(*conditions).scalar() or 0
    rows = (
        db.query(Problem)
        .options(joinedload(Problem.teacher))
        .filter(*conditions)
        .order_by(Problem.created_at.desc(), Problem.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    counts = _problem_counts(db, [problem.id for problem in rows])
    summaries = [_problem_summary(problem, counts) for problem in rows]
    return ProblemListResponse(total=int(total), count=len(summaries), problems=summaries)


@app.post(
    "/api/problems",
    response_model=ProblemDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a problem",
)
async def create_problem(
    payload: ProblemCreate,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ProblemDetail:
    """Create a problem with its sample and hidden test cases.

    ``classroom_id`` publishes the exercise to a class; leave it out to keep the
    problem in the private bank. A teacher may only publish to a class they own.
    """
    if payload.classroom_id is not None:
        classroom = db.get(Classroom, payload.classroom_id)
        if classroom is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Class not found."
            )
        if not teacher.is_admin and classroom.teacher_id != teacher.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only publish problems to a class you teach.",
            )

    default_starter_c = payload.starter_code_c
    default_starter_cpp = payload.starter_code_cpp
    problem = Problem(
        title=payload.title,
        description=payload.description,
        difficulty=payload.difficulty,
        time_limit_sec=payload.time_limit_sec,
        memory_limit_mb=payload.memory_limit_mb,
        teacher_id=teacher.id,
        classroom_id=payload.classroom_id,
        starter_code_c=DEFAULT_STARTER_C if default_starter_c is None else default_starter_c,
        starter_code_cpp=(
            DEFAULT_STARTER_CPP if default_starter_cpp is None else default_starter_cpp
        ),
    )
    problem.test_cases = [
        TestCase(
            input=case_row.input,
            expected_output=case_row.expected_output,
            is_sample=case_row.is_sample,
        )
        for case_row in payload.test_cases
    ]
    db.add(problem)
    db.commit()
    db.refresh(problem)
    logger.info(
        "teacher id=%s created problem %r (id=%s, %d test cases)",
        teacher.id,
        problem.title,
        problem.id,
        len(problem.test_cases),
    )
    return _problem_detail(problem, reveal_hidden=True)


@app.get(
    "/api/problems/{problem_id}",
    response_model=ProblemDetail,
    summary="Problem detail (sample cases only)",
)
async def read_problem(
    problem_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemDetail:
    """Statement, limits, starter code and the **sample** test cases.

    Hidden cases are counted (``hidden_count``) but never disclosed, so the
    response cannot be used to harvest the grading data.
    """
    problem = _load_problem(db, problem_id)
    _assert_problem_visible(problem, user, db)
    return _problem_detail(problem, reveal_hidden=_can_manage_problem(problem, user))


@app.post(
    "/api/problems/{problem_id}/testcases",
    response_model=ProblemDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Append a test case",
)
async def add_problem_test_case(
    problem_id: int,
    payload: TestCaseIn,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ProblemDetail:
    """Add one test case to a problem you own (or any problem, as admin)."""
    problem = _load_problem(db, problem_id)
    if not _can_manage_problem(problem, teacher):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the teacher who created a problem may change its test data.",
        )
    if len(problem.test_cases) >= MAX_TEST_CASES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A problem may carry at most {MAX_TEST_CASES} test cases.",
        )

    db.add(
        TestCase(
            problem_id=problem.id,
            input=payload.input,
            expected_output=payload.expected_output,
            is_sample=payload.is_sample,
        )
    )
    db.commit()
    db.refresh(problem)
    return _problem_detail(problem, reveal_hidden=True)


@app.post(
    "/api/problems/{problem_id}/submit",
    response_model=SubmissionResult,
    summary="Judge a submission against every test case",
)
async def submit_to_problem(
    request: Request,
    problem_id: int,
    payload: SubmissionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubmissionResult:
    """Run ``code`` against **all** test cases of the problem and grade it.

    Scoring: every case is worth the same, so ``score`` is the percentage of
    passed cases. Output comparison follows :func:`_normalize_output`. Sample
    cases come back with full detail; hidden cases come back as verdicts plus
    ``hidden_summary`` only. A student's attempt is stored as a
    :class:`models.Submission`; a teacher's or admin's run is a ``trial_run`` and
    is not recorded, keeping class statistics about students clean.

    The whole run holds one of the ``MAX_CONCURRENT_RUNS`` judge slots, so the
    wall-clock cost (up to ``test_cases x (compile + time_limit)``) is bounded and
    shared fairly with ``POST /api/run``.
    """
    problem = _load_problem(db, problem_id)
    _assert_problem_visible(problem, user, db)

    cases = list(problem.test_cases)
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This problem has no test cases yet, so it cannot be graded.",
        )

    timeout = min(max(float(problem.time_limit_sec), MIN_TIME_LIMIT_SEC), MAX_TIME_LIMIT_SEC)
    async with _execution_slots(request):
        report = await run_in_threadpool(
            _judge_all_cases, payload.code, payload.language, cases, timeout
        )

    if report.judge_error:
        # The sandbox itself failed (missing compiler, unwritable tmpfs, ...).
        # Nothing was measured, so nothing is recorded.
        logger.error(
            "judge error while grading problem %s for user %s: %s",
            problem_id,
            user.id,
            report.judge_error,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The judge could not execute this submission; nothing was recorded.",
        )

    total_count = len(cases)
    passed_count = report.passed_count()
    verdict = report.aggregate_status(total_count)
    score = round(100.0 * passed_count / total_count, 2) if total_count else 0.0
    trial_run = not user.is_student
    submission_id: Optional[int] = None

    if not trial_run:
        submission = Submission(
            problem_id=problem.id,
            student_id=user.id,
            language=payload.language,
            code=payload.code,
            status=verdict,
            score=score,
            passed_count=passed_count,
            total_count=total_count,
            execution_time_ms=report.slowest_ms(),
        )
        db.add(submission)
        try:
            db.commit()
            db.refresh(submission)
            submission_id = submission.id
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The problem or your account disappeared mid-grading.",
            ) from None

    hidden_cases = [outcome for outcome in report.outcomes if not outcome.is_sample]
    hidden_passed = sum(1 for outcome in hidden_cases if outcome.passed)
    logger.info(
        "graded problem %s for %s: status=%s score=%.2f (%d/%d)%s",
        problem_id,
        user.username,
        verdict,
        score,
        passed_count,
        total_count,
        " [trial run]" if trial_run else "",
    )

    return SubmissionResult(
        submission_id=submission_id,
        problem_id=problem.id,
        student_id=user.id,
        language=payload.language,
        status=verdict,  # type: ignore[arg-type]
        score=score,
        passed_count=passed_count,
        total_count=total_count,
        execution_time_ms=report.slowest_ms(),
        compile_time_ms=report.compile_time_ms,
        compile_output=report.compile_output,
        time_limit_sec=problem.time_limit_sec,
        memory_limit_mb=problem.memory_limit_mb,
        trial_run=trial_run,
        results=[
            _case_verdict(outcome, _can_manage_problem(problem, user))
            for outcome in report.outcomes
        ],
        hidden_summary=HiddenSummary(
            total=len(hidden_cases),
            passed=hidden_passed,
            failed=len(hidden_cases) - hidden_passed,
        ),
    )


# --------------------------------------------------------------------------- #
# Submission history
# --------------------------------------------------------------------------- #


def _submission_conditions(db: Session, user: User) -> tuple[list, list[int]]:
    """Return ``(conditions, manageable_class_ids)`` for the submission history.

    A student sees only their own rows. A teacher sees attempts on problems they
    created **plus** attempts by students of any class they teach. Admins see all.
    """
    if user.is_admin:
        return [], []
    if user.is_teacher:
        class_ids = _teacher_classroom_ids(db, user)
        enrolled: list[int] = []
        if class_ids:
            enrolled = [
                int(row[0])
                for row in db.query(ClassroomMembership.student_id)
                .filter(ClassroomMembership.classroom_id.in_(class_ids))
                .distinct()
                .all()
            ]
        own_problem_ids = [
            int(row[0])
            for row in db.query(Problem.id).filter(Problem.teacher_id == user.id).all()
        ]
        conditions = [
            or_(
                Submission.problem_id.in_(own_problem_ids),
                Submission.student_id.in_(enrolled),
            )
        ]
        return conditions, class_ids
    return [Submission.student_id == user.id], _student_classroom_ids(db, user)


@app.get("/api/submissions", response_model=SubmissionListResponse, summary="Submission history")
async def list_submissions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    problem_id: Optional[int] = Query(default=None, ge=1),
    classroom_id: Optional[int] = Query(default=None, ge=1, description="Restrict to one class."),
    student_id: Optional[int] = Query(default=None, ge=1, description="Teacher/admin only."),
    verdict: Optional[SubmissionStatusLiteral] = Query(default=None, alias="status"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> SubmissionListResponse:
    """Submission history, newest first.

    Students get their own attempts. A teacher may pass ``classroom_id`` or
    ``student_id`` to look across their class, but only for classes they teach -
    anything else is a ``403``.
    """
    conditions, class_ids = _submission_conditions(db, user)

    if student_id is not None:
        if user.is_student and student_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students may only read their own submissions.",
            )
        if user.is_teacher:
            enrolled_in_my_classes: set[int] = set()
            if class_ids:
                enrolled_in_my_classes = {
                    int(row[0])
                    for row in db.query(ClassroomMembership.student_id)
                    .filter(ClassroomMembership.classroom_id.in_(class_ids))
                    .all()
                }
            if student_id not in enrolled_in_my_classes and student_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="That student does not attend one of your classes.",
                )
        conditions.append(Submission.student_id == student_id)

    if classroom_id is not None:
        if not user.is_admin and classroom_id not in class_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not teach that class.",
            )
        conditions.append(
            Submission.problem_id.in_(
                [
                    int(row[0])
                    for row in db.query(Problem.id)
                    .filter(Problem.classroom_id == classroom_id)
                    .all()
                ]
            )
        )

    if problem_id is not None:
        conditions.append(Submission.problem_id == problem_id)
    if verdict is not None:
        conditions.append(Submission.status == verdict)

    total = db.query(func.count(Submission.id)).filter(*conditions).scalar() or 0
    rows = (
        db.query(Submission)
        .options(joinedload(Submission.problem), joinedload(Submission.student))
        .filter(*conditions)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    payloads = [
        SubmissionOut(
            id=row.id,
            problem_id=row.problem_id,
            problem_title=row.problem.title if row.problem else "",
            student_id=row.student_id,
            student_username=row.student.username if row.student else "",
            student_name=row.student.display_name if row.student else "",
            language=row.language,
            status=row.status,  # type: ignore[arg-type]
            score=row.score,
            passed_count=row.passed_count,
            total_count=row.total_count,
            execution_time_ms=row.execution_time_ms,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return SubmissionListResponse(total=int(total), count=len(payloads), submissions=payloads)


@app.get(
    "/api/submissions/{submission_id}",
    response_model=SubmissionDetailOut,
    summary="One submission with its source",
)
async def read_submission(
    submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SubmissionDetailOut:
    """A single recorded attempt, including the submitted source code.

    Restricted to the author, the problem's teacher and admins - the source is
    the student's own work and is not exposed to other students.
    """
    submission = (
        db.query(Submission)
        .options(joinedload(Submission.problem), joinedload(Submission.student))
        .filter(Submission.id == submission_id)
        .one_or_none()
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found."
        )

    problem = submission.problem
    allowed = user.is_admin or submission.student_id == user.id
    if not allowed and problem is not None:
        allowed = problem.teacher_id == user.id or (
            problem.classroom_id is not None
            and problem.classroom_id in _teacher_classroom_ids(db, user)
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may not review this submission.",
        )

    return SubmissionDetailOut(
        id=submission.id,
        problem_id=submission.problem_id,
        problem_title=problem.title if problem else "",
        student_id=submission.student_id,
        student_username=submission.student.username if submission.student else "",
        student_name=submission.student.display_name if submission.student else "",
        language=submission.language,
        status=submission.status,  # type: ignore[arg-type]
        score=submission.score,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        execution_time_ms=submission.execution_time_ms,
        created_at=submission.created_at,
        code=submission.code,
    )


if __name__ == "__main__":  # pragma: no cover - developer convenience
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8010")),
        reload=bool(os.environ.get("RELOAD", "")),
    )
