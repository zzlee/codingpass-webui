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
``GET  /api/classrooms/{id}/gradebook.csv``
                                     best-score-per-problem grid for the class,
                                     as a spreadsheet-ready CSV (staff only).

**Problem bank**
``GET  /api/problems``               problems visible to the caller.
``POST /api/problems``               teacher creates a problem with test cases.
``GET  /api/problems/{id}``          detail + starter code + *sample* cases only.
``GET  /api/problems/{id}/pdf``       stream the pack's statement PDF inline.
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
import csv
import io
import json
import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, ValidationError, field_validator
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

try:  # package-style launch: `uvicorn backend.main:app`
    from .compiler import (
        DEFAULT_TIMEOUT_SECONDS,
        MEMORY_LIMIT_BYTES,
        Status,
        RunResult,
        limits_summary,
        run_code,
        run_code_batch,
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
        run_code_batch,
        supported_languages,
        toolchain_versions,
    )

try:  # package-style launch: `uvicorn backend.main:app`
    from .ai_tutor import (
        AI_PROVIDER,
        HEURISTIC_MODEL_NAME,
        VLLM_BASE_URL,
        VLLM_MODEL,
        analyze_code,
        check_ai_status,
    )
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from ai_tutor import (  # type: ignore[no-redef]
        AI_PROVIDER,
        HEURISTIC_MODEL_NAME,
        VLLM_BASE_URL,
        VLLM_MODEL,
        analyze_code,
        check_ai_status,
    )

try:  # package-style launch: `uvicorn backend.main:app`
    from .patch_probe import (
        BROKEN_ALGORITHM_SCORE,
        CLEAN_PASS_SCORE,
        MAX_PROBE_RUNS,
        MAX_PROBE_SECONDS,
        PATCHED_PASS_SCORE,
        probe_minimal_patch,
    )
    from .plagiarism import compute_similarity_pairs
    from .problem_pack import ProblemPackError, parse_problem_pack
    from .teda_engine import DualRunError, TeDaError, compare_outputs
    from .wording_rules import check_wording_rules
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from patch_probe import (  # type: ignore[no-redef]
        BROKEN_ALGORITHM_SCORE,
        CLEAN_PASS_SCORE,
        MAX_PROBE_RUNS,
        MAX_PROBE_SECONDS,
        PATCHED_PASS_SCORE,
        probe_minimal_patch,
    )
    from plagiarism import compute_similarity_pairs  # type: ignore[no-redef]
    from problem_pack import ProblemPackError, parse_problem_pack  # type: ignore[no-redef]
    from teda_engine import DualRunError, TeDaError, compare_outputs  # type: ignore[no-redef]
    from wording_rules import check_wording_rules  # type: ignore[no-redef]

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
    from .database import init_db, utcnow
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
    from database import init_db, utcnow  # type: ignore[no-redef]
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
#: Test cases per problem. A submission is compiled once and then run case by
#: case, so this bounds the worst-case latency of
#: ``POST /api/problems/{id}/submit``
#: (``compile + test_cases x time_limit``).
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

# --------------------------------------------------------------------------- #
# Problem-pack import + Stage 3/4 configuration
# --------------------------------------------------------------------------- #

#: Largest uploaded pack archive, in bytes (``POST /api/problems/import-pack``).
MAX_PACK_UPLOAD_BYTES = max(1024, int(os.environ.get("MAX_PACK_UPLOAD_BYTES", str(64 * 1024 * 1024))))


def _pack_pdf_dir() -> Path:
    """Where a statement PDF extracted from an uploaded archive is persisted."""
    return Path(
        os.environ.get("PROBLEM_PACK_PDF_DIR", "").strip()
        or (Path(__file__).resolve().parent / "problem_packs")
    )


def _pack_allowed_roots() -> list[Path]:
    """Directories a teacher may import a pack *folder* from.

    ``PROBLEM_PACK_ROOTS`` is a comma separated allow-list of server directories.
    An upload carries its own pack, so the allow-list only guards the
    ``directory_path`` form of ``POST /api/problems/import-pack``: without it a
    teacher account could point the importer at any readable folder on the
    server. Left unset the check is disabled (the shape any single-teacher
    classroom server already has), and every import logs the resolved path.
    """
    raw = os.environ.get("PROBLEM_PACK_ROOTS", "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            roots.append(Path(entry).resolve())
        except OSError:  # pragma: no cover - a malformed root is simply ignored
            logger.warning("ignoring unreadable PROBLEM_PACK_ROOTS entry %r", entry)
    return roots


PACK_ALLOWED_ROOTS: list[Path] = _pack_allowed_roots()

#: Default escalation threshold of the plagiarism screen.
PLAGIARISM_DEFAULT_THRESHOLD = 0.85

#: Most submissions one plagiarism screen compares. Similarity is all-pairs, so
#: this bounds the endpoint's worst case on a very large class; the response says
#: ``truncated: true`` when it stopped early.
PLAGIARISM_MAX_SUBMISSIONS = max(
    2, int(os.environ.get("PLAGIARISM_MAX_SUBMISSIONS", "200"))
)

#: The 100-point teaching rubric. ``sample``/``random`` are scaled by how many
#: cases actually exist; see :func:`_build_rubric`.
RUBRIC_COMPILE_POINTS = 10.0
RUBRIC_SAMPLE_POINTS = 10.0
RUBRIC_RANDOM_POINTS = 30.0
RUBRIC_LOGIC_POINTS = 40.0
RUBRIC_QUALITY_POINTS = 10.0


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
        description=(
            "Address-space budget (RLIMIT_AS) enforced for every submission to "
            "this problem; defaults to the sandbox ceiling CODE_MEMORY_LIMIT_MB."
        ),
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
    """Compact problem row used in lists (no description, no test cases).

    The ``has_*`` flags expose only *whether* a problem pack carries a given
    attachment - never the reference source, the rule text or the PDF path.
    """

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
    pdf_path: Optional[str] = Field(
        default=None,
        description=(
            "Server path of the statement PDF, **staff only**: students get the "
            "problem's own ``/pdf`` endpoint instead, and must not learn the "
            "layout of the server's filesystem."
        ),
    )
    wording_rules: Optional[str] = Field(
        default=None,
        description=(
            "TeDa-Wording rule text. Present only for the problem's own teacher "
            "or an admin: students must be able to *satisfy* the rules, and the "
            "verdict already quotes any rule they broke."
        ),
    )


class ProblemListResponse(BaseModel):
    total: int
    count: int
    problems: list[ProblemSummary]


class ImportPackRequest(BaseModel):
    """Body of ``POST /api/problems/import-pack`` (JSON form).

    Exactly one of :attr:`directory_path` (a pack folder or ``.zip`` already on
    the server) and an uploaded ``file`` part (multipart form) must be supplied.
    """

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
        description=(
            "Server-side path to a `Pxxxx_題名` pack folder or a .zip of one. "
            "Restricted to PROBLEM_PACK_ROOTS when that variable is set."
        ),
    )
    classroom_id: Optional[int] = Field(
        default=None, ge=1,
        description="Publish to this class; omit to keep the problem in your bank.",
    )
    generate_hidden: bool = Field(
        default=True,
        description="Run the TeDa generator x reference dual run to build hidden cases.",
    )
    num_random: int = Field(
        default=30, ge=1, le=MAX_TEST_CASES,
        description="How many random hidden cases to generate.",
    )
    seed: int = Field(
        default=42,
        description=(
            "Base seed of the TeDa generator (case i uses seed*1000+i). Keep the "
            "default to reproduce the same hidden case set as the offline sandbox."
        ),
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
    presentation_warning: bool = Field(
        default=False,
        description=(
            "This case passed, but only because the judge ignores trailing "
            "whitespace: a byte-strict judge would answer Presentation Error."
        ),
    )
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


class RubricScores(BaseModel):
    """The 100-point teaching rubric, as measured for one submission.

    ``score`` on a submission stays the platform-style pass percentage; this is
    the breakdown the offline rubric defines (``compile 10 + sample 10 +
    random 30 + logic 40 + quality 10``), so a student who was one semicolon
    away from a working program is not reported as a zero.
    """

    compile_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_COMPILE_POINTS)
    sample_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_LOGIC_POINTS)
    random_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_RANDOM_POINTS)
    logic_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_LOGIC_POINTS)
    quality_score: float = Field(default=0.0, ge=0.0, le=RUBRIC_QUALITY_POINTS)
    total: float = Field(default=0.0, ge=0.0, le=100.0, description="Sum, clamped to 100.")


class PatchProbeReport(BaseModel):
    """What :func:`patch_probe.probe_minimal_patch` concluded about a submission."""

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

    # -- Stage 3 / 4: rubric, formatting and wording --------------------------- #
    rubric: RubricScores = Field(default_factory=RubricScores)
    presentation_warning: bool = Field(
        default=False,
        description="Passed, but only under the whitespace-tolerant judge (PE).",
    )
    wording_pass: bool = Field(
        default=True,
        description="True when the problem declares no wording rules at all.",
    )
    wording_violations: list[str] = Field(
        default_factory=list,
        description="Human-readable violated `#term`/`#root`/`#regex` rules.",
    )
    patch_probe: Optional[PatchProbeReport] = Field(
        default=None,
        description=(
            "Minimal Patch Probe result. Only present when the submission did "
            "not compile or did not pass every case: an accepted submission "
            "needs no probe."
        ),
    )


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

    # -- Phase 4: AI + teacher review (``None`` until somebody reviews it) --- #
    ai_feedback: Optional[str] = Field(
        default=None, description="Markdown review produced by the AI tutor."
    )
    ai_suggested_score: Optional[float] = Field(
        default=None, description="Advisory score suggested by the AI (0-100)."
    )
    ai_model: Optional[str] = Field(
        default=None, description="Model that produced ``ai_feedback``."
    )
    ai_reviewed_at: Optional[UtcDatetime] = None
    teacher_feedback: Optional[str] = Field(
        default=None, description="Teacher's written feedback."
    )
    teacher_score: Optional[float] = Field(
        default=None, description="Teacher's manual score (0-100)."
    )
    reviewed_by_id: Optional[int] = Field(
        default=None, description="Teacher/admin account that graded the attempt."
    )
    reviewed_at: Optional[UtcDatetime] = None

    # -- Stage 4: teaching rubric --------------------------------------------- #
    rubric_total: float = Field(
        default=0.0,
        description=(
            "Sum of the five rubric parts (compile/sample/random/logic/quality). "
            "``score`` stays the pass percentage; this is the 100-point rubric. "
            "Attempts recorded before Stage 4 read 0.0."
        ),
    )


class SubmissionListResponse(BaseModel):
    total: int
    count: int
    submissions: list[SubmissionOut]


class SubmissionDetailOut(SubmissionOut):
    """One recorded attempt, including the submitted source for review."""

    code: str

    # -- Stage 3 / 4: rubric breakdown and formatting flags -------------------- #
    rubric: RubricScores = Field(default_factory=RubricScores)
    presentation_warning: bool = False
    wording_pass: bool = True
    wording_violations: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 3: plagiarism screen schemas
# --------------------------------------------------------------------------- #


class PlagiarismPair(BaseModel):
    """One suspicious pair, as measured by :mod:`plagiarism`.

    ``ast_similarity`` is the structural number: identifiers renamed, comments
    and includes stripped, ``for``/``while`` re-spelling and literal values
    normalised away, so renaming ``count`` to ``cnt`` does not lower it.
    ``token_similarity`` keeps the real identifier names, which is what tells a
    reviewer whether two students shared a keyboard or merely shared a lecture.
    ``exemption`` explains a ``flagged=False`` pair: both submissions match the
    teacher's reference solution, so the match is explained by the model answer
    (Reference Baseline Subtraction) instead of by copying.
    """

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
    threshold: float = Field(description="Escalation cut-off this screen used.")
    reference_available: bool = Field(
        default=False,
        description=(
            "Whether the problem carries a reference solution, i.e. whether "
            "Reference Baseline Subtraction could run at all."
        ),
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Languages screened; submissions of different languages are never compared.",
    )
    submissions_compared: int = 0
    students_compared: int = 0
    pair_count: int = Field(default=0, description="Pairs at or above the threshold.")
    flagged_count: int = 0
    exempt_count: int = Field(
        default=0, description="Threshold hits cleared as 'both wrote the model answer'."
    )
    skipped_trivial: int = Field(
        default=0,
        description=(
            "Submissions left out because they are empty, whitespace-only or "
            "untouched starter scaffolding: comparing scaffolding reports noise."
        ),
    )
    truncated: bool = Field(
        default=False,
        description=(
            f"True when more submissions existed than the screen compares "
            f"(PLAGIARISM_MAX_SUBMISSIONS), so the pair list is not exhaustive."
        ),
    )
    pairs: list[PlagiarismPair] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Phase 4: AI tutor schemas
# --------------------------------------------------------------------------- #


class AIStatusResponse(BaseModel):
    """Reachability of the configured vLLM tutor."""

    available: bool
    provider: str
    model: str
    base_url: str
    detail: str = ""


class AIAnalyzeRequest(BaseModel):
    """Ad-hoc review request (Run panel / teacher preview, nothing persisted)."""

    code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    language: LanguageLiteral
    status: Optional[str] = Field(
        default="unknown", description="Judge verdict for this source, if known."
    )
    compile_output: Optional[str] = ""
    stderr: Optional[str] = ""
    problem_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Attach statement context. Requires authentication; test cases are never sent.",
    )


class AIAnalyzeResponse(BaseModel):
    """Structured review produced by :func:`ai_tutor.analyze_code`."""

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
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
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
        # -- Stage 3/4: what the teacher-facing screens need to label --------- #
        "rubric_points": {
            "compile": RUBRIC_COMPILE_POINTS,
            "sample": RUBRIC_SAMPLE_POINTS,
            "random": RUBRIC_RANDOM_POINTS,
            "logic": RUBRIC_LOGIC_POINTS,
            "quality": RUBRIC_QUALITY_POINTS,
        },
        "plagiarism_default_threshold": PLAGIARISM_DEFAULT_THRESHOLD,
        "patch_probe": {
            "max_probe_runs": MAX_PROBE_RUNS,
            "max_probe_seconds": MAX_PROBE_SECONDS,
            "clean_pass_score": CLEAN_PASS_SCORE,
            "patched_pass_score": PATCHED_PASS_SCORE,
            "broken_algorithm_score": BROKEN_ALGORITHM_SCORE,
        },
        "pack_upload_bytes": MAX_PACK_UPLOAD_BYTES,
        "pack_roots_restricted": bool(PACK_ALLOWED_ROOTS),
        # Configuration only: whether the tutor is actually *reachable* needs a
        # live probe, which is what ``GET /api/ai/status`` is for.
        "ai": {
            "provider": AI_PROVIDER,
            "model": VLLM_MODEL,
            "base_url": VLLM_BASE_URL,
            "heuristic_model": HEURISTIC_MODEL_NAME,
            "status_path": "/api/ai/status",
        },
        "local_agent": {
            "default_port": 8765,
            "websocket_url": "ws://127.0.0.1:8765",
            "download_url": "/api/agent/download",
            "version": "1.0.0",
        },
    }


@app.get("/api/agent/info")
async def agent_info() -> dict:
    """Connection info for the CodingPass Local Agent."""
    return {
        "host": "127.0.0.1",
        "port": 8765,
        "websocket_url": "ws://127.0.0.1:8765",
        "download_url": "/api/agent/download",
        "version": "1.0.0",
    }


@app.get("/api/agent/download")
async def download_agent_package():
    """Download a standalone zip containing the Local Agent runner.

    Public on purpose: a student needs the runner before they have anything to
    authenticate with. The file list is fixed, so this is not a file reader, and
    a server without the ``local_agent`` folder answers ``404`` instead of
    handing out a valid-but-empty archive.
    """
    agent_dir = Path(__file__).resolve().parent.parent / "local_agent"
    members = [
        (agent_dir / name, name)
        for name in ("agent.py", "run_agent.sh", "run_agent.bat", "README.md")
        if (agent_dir / name).is_file()
    ]
    if not any(name == "agent.py" for _, name in members):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The Local Agent package is not installed on this server.",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath, name in members:
            zf.write(fpath, arcname=f"codingpass-agent/{name}")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="codingpass-agent.zip"',
            # The runner executes on a student machine: never serve a stale copy
            # from a shared cache.
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    #: Ran and was judged correct, but is not byte-identical (trailing spaces,
    #: stray blank lines): the TeDa judge trims those, a strict judge PE's them.
    presentation_warning: bool = False


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

    def sample_outcomes(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.outcomes if outcome.is_sample]

    def hidden_outcomes(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.is_sample]

    def presentation_warning(self) -> bool:
        """True when any *passing* case only passed thanks to trimmed output."""
        return any(
            outcome.passed and outcome.presentation_warning for outcome in self.outcomes
        )

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
    memory_limit_mb: Optional[int] = None,
) -> JudgeReport:
    """Compile ``code`` **once**, run it on every test case and score the outputs.

    Runs in a worker thread. A compile failure short-circuits the loop (every
    case would fail identically); a sandbox ``internal_error`` aborts the whole
    run because the environment, not the submission, is broken. Timing out or
    crashing on one case does **not** stop the remaining cases: a partially
    correct program still earns credit for the cases it survived.

    ``memory_limit_mb`` is the problem's advertised budget, handed to the sandbox
    verbatim so the statement, the UI badge and the ``RLIMIT_AS`` the student hits
    are the same number (``None`` falls back to the global ceiling).

    Compilation happens once, through :func:`compiler.run_code_batch`, which
    builds the binary a single time in one isolated temporary directory and then
    executes it once per test case in a private sub-directory. Worst-case latency
    is therefore ``compile + len(cases) * timeout_seconds`` instead of
    ``len(cases) * (compile + timeout_seconds)``; ``MAX_TEST_CASES`` still bounds
    the run phase.
    """
    report = JudgeReport()
    results: list[RunResult] = run_code_batch(
        code,
        language,
        [case_row.input for case_row in cases],
        timeout_seconds,
        memory_limit_mb=memory_limit_mb,
    )
    if len(results) != len(cases):
        # Alignment is the contract of the batch API. A short answer must never
        # be graded: silently dropping cases would change the score.
        report.judge_error = _clip(
            f"the sandbox returned {len(results)} verdict(s) for "
            f"{len(cases)} test case(s)"
        )
        return report
    for case_row, result in zip(cases, results):
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
        # Two comparison layers, mirroring the offline judge:
        #   * pass/fail uses :func:`_normalize_output`, the platform's
        #     whitespace-tolerant rule (``trim`` + ``trimEnd``),
        #   * :func:`compare_outputs` adds the byte-strict layer, and the gap
        #     between the two *is* the presentation warning - the answer is
        #     correct, the layout is not what the statement asked for.
        comparison = compare_outputs(result.stdout, expected)
        passed = result.status == Status.SUCCESS and _normalize_output(
            result.stdout
        ) == _normalize_output(expected)
        presentation_warning = bool(passed and not comparison["strict_match"])
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
                presentation_warning=presentation_warning,
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
        # A hidden case's PE flag says nothing about its data, but the flag only
        # means anything next to the output that caused it, so it stays with the
        # rest of the case detail.
        presentation_warning=bool(outcome.presentation_warning and visible),
        input=outcome.stdin if visible else None,
        expected_output=outcome.expected_output if visible else None,
        stdout=outcome.stdout if visible else None,
        stderr=(_clip(outcome.stderr) if visible else "") or None,
    )


def _probe_cases(cases: list[TestCase], limit: int = 3) -> list[dict[str, str]]:
    """Pick the public cases the Minimal Patch Probe is allowed to run.

    Only sample cases: the probe recompiles and reruns a repaired program, and
    feeding it hidden expected outputs would turn a rescue heuristic into an
    oracle. A problem whose cases are all hidden falls back to its first case,
    which still only proves the repaired build *runs*.
    """
    picked = [case for case in cases if case.is_sample][:limit]
    if not picked and cases:
        picked = cases[:1]
    return [
        {"input": case.input or "", "expected_output": case.expected_output or ""}
        for case in picked
    ]


def _probe_public(probe: Optional[dict]) -> Optional[PatchProbeReport]:
    """Project a probe result onto the wire format.

    ``patched_code`` and ``compile_output`` are deliberately **dropped**: a
    student who was one semicolon short of a working program must not receive
    the working program back from the grading response.
    """
    if not probe:
        return None
    return PatchProbeReport(
        needed=bool(probe.get("needed")),
        patch_success=bool(probe.get("patch_success")),
        probed_logic_score=float(probe.get("probed_logic_score") or 0.0),
        details=str(probe.get("details") or ""),
        baseline_status=str(probe.get("baseline_status") or ""),
        applied_fixes=[str(name) for name in probe.get("applied_fixes") or []],
        attempted_fixes=[str(name) for name in probe.get("attempted_fixes") or []],
        cases_tested=int(probe.get("cases_tested") or 0),
        cases_passed=int(probe.get("cases_passed") or 0),
        stopped_early=bool(probe.get("stopped_early")),
    )


@dataclass
class Rubric:
    """The 100-point teaching rubric for one graded submission.

    Mirrors ``評分系統計劃書_v1.md`` §6: ``compile 10 + sample 10 + random 30 +
    logic 40 + quality 10``. See :func:`_build_rubric` for how each part is
    measured.
    """

    compile_score: float = 0.0
    sample_score: float = 0.0
    random_score: float = 0.0
    logic_score: float = 0.0
    quality_score: float = 0.0

    @property
    def total(self) -> float:
        return round(
            min(100.0,
                max(
                    0.0,
                    self.compile_score
                    + self.sample_score
                    + self.random_score
                    + self.logic_score
                    + self.quality_score,
                ),
            ),
            2,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "compile_score": self.compile_score,
            "sample_score": self.sample_score,
            "random_score": self.random_score,
            "logic_score": self.logic_score,
            "quality_score": self.quality_score,
            "total": self.total,
        }


def _build_rubric(
    report: JudgeReport,
    verdict: str,
    *,
    presentation_warning: bool,
    wording_pass: bool,
    probe: Optional[dict] = None,
    trial_run: bool = False,
) -> Rubric:
    """Turn one :class:`JudgeReport` into the five rubric parts.

    Rules, in the order they are applied:

    ``compile_score`` (10)
        ``10.0`` when the source built, else ``0.0``. A build failure zeroes the
        two case bands as well, exactly like the offline pipeline.
    ``sample_score`` (10)
        Passed public samples scaled to 10 - or to 40 when the problem has no
        hidden cases at all, so the rubric still adds up to 100 and a sample-only
        problem is not capped at 70.
    ``random_score`` (30)
        Passed hidden cases scaled to 30; ``0.0`` when there are none (the sample
        band already absorbed them).
    ``logic_score`` (40)
        ``40.0`` for an accepted submission. Otherwise the larger of the measured
        pass ratio (``40 * passed / total``) and the Minimal Patch Probe verdict,
        with the probe capped at :data:`~patch_probe.PATCHED_PASS_SCORE` so only
        a fully accepted run reaches 40. A presentation warning caps this band at
        36 either way. A trial run never probes (the teacher is previewing, not
        being graded), so a failed trial build scores 0 here.
    ``quality_score`` (10)
        Starts at 10; -3 for a wording-rule violation, -1 for compiler warnings,
        -1 for a case that timed out or crashed, and capped at 5 when nothing
        built (the rubric's CE ceiling). A presentation warning is *not* charged
        here: it already cost part of the logic band.
    """
    samples = report.sample_outcomes()
    hidden = report.hidden_outcomes()
    total_cases = len(report.outcomes)
    passed_cases = report.passed_count()

    compile_score = 0.0 if report.compile_failed else RUBRIC_COMPILE_POINTS

    samples_passed = sum(1 for outcome in samples if outcome.passed)
    if hidden:
        sample_band = RUBRIC_SAMPLE_POINTS
        random_score = _scale(passed_hidden(hidden), RUBRIC_RANDOM_POINTS, len(hidden))
    else:
        # No random band to award: the sample band carries the whole 40 points.
        sample_band = RUBRIC_SAMPLE_POINTS + RUBRIC_RANDOM_POINTS
        random_score = 0.0
    sample_score = _scale(samples_passed, sample_band, len(samples))

    measured = _scale(passed_cases, RUBRIC_LOGIC_POINTS, total_cases)
    if verdict == SubmissionStatus.ACCEPTED:
        logic_score = RUBRIC_LOGIC_POINTS
    else:
        probed = 0.0
        if probe and probe.get("patch_success"):
            probed = min(float(probe.get("probed_logic_score") or 0.0), PATCHED_PASS_SCORE)
        logic_score = round(max(measured, probed), 2)
        if report.compile_failed:
            # Nothing ran: only the probe may award credit above the CE floor.
            logic_score = round(min(logic_score, PATCHED_PASS_SCORE), 2)
    if presentation_warning:
        # A PE answer loses the I/O-format sub-criterion of the logic band. This
        # is charged once, here, and is why a PE submission lands at 36/40
        # instead of a full 40 or a bare pass.
        logic_score = round(min(logic_score, RUBRIC_LOGIC_POINTS - 4.0), 2)

    quality = RUBRIC_QUALITY_POINTS
    if presentation_warning:
        quality -= 2.0
    if not wording_pass:
        quality -= 3.0
    statuses = {outcome.status for outcome in report.outcomes}
    if Status.TIME_LIMIT_EXCEEDED in statuses or Status.RUNTIME_ERROR in statuses:
        quality -= 1.0
    if report.compile_failed:
        quality = min(quality, 5.0)
    if trial_run and report.compile_failed:
        quality = min(quality, 5.0)
    quality_score = round(min(RUBRIC_QUALITY_POINTS, max(0.0, quality)), 2)

    return Rubric(
        compile_score=round(compile_score, 2),
        sample_score=round(sample_score, 2),
        random_score=round(random_score, 2),
        logic_score=round(logic_score, 2),
        quality_score=quality_score,
    )


def _scale(passed: int, band: float, total: int) -> float:
    """``passed / total * band``, ``0.0`` when the band has nothing to measure."""
    if total <= 0:
        return 0.0
    return round(band * passed / total, 2)


def passed_hidden(hidden: list[CaseOutcome]) -> int:
    return sum(1 for outcome in hidden if outcome.passed)


#: Keys of the wording verdict that are internal to :mod:`wording_rules`.
def _wording_json(violations: Optional[list[str]]) -> Optional[str]:
    """Serialise the human-readable violation list for ``wording_violations``."""
    if not violations:
        return None
    try:
        return json.dumps(list(violations), ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return json.dumps([str(item) for item in violations])


def _wording_list(stored: Optional[str]) -> list[str]:
    """Decode ``Submission.wording_violations`` back into a display list."""
    if not stored:
        return []
    try:
        decoded = json.loads(stored)
    except ValueError:
        # A hand-edited row still has to render.
        return [stored]
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return [str(decoded)]


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
        problem_code=problem.problem_code,
        has_ref_code=problem.has_ref_code,
        has_wording_rules=problem.has_wording_rules,
        has_random_generator=problem.has_random_generator,
        has_statement_pdf=problem.has_statement_pdf,
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
        problem_code=problem.problem_code,
        has_ref_code=problem.has_ref_code,
        has_wording_rules=problem.has_wording_rules,
        has_random_generator=problem.has_random_generator,
        has_statement_pdf=problem.has_statement_pdf,
        description=problem.description,
        starter_code_c=problem.starter_code_c,
        starter_code_cpp=problem.starter_code_cpp,
        # An absolute server path. Students fetch the document through
        # ``GET /api/problems/{id}/pdf``; handing them the path only tells an
        # attacker where the platform keeps its files (and what it is called).
        pdf_path=problem.pdf_path if reveal_hidden else None,
        test_cases=[
            TestCasePublic(
                id=case_row.id,
                input=case_row.input,
                expected_output=case_row.expected_output,
                is_sample=case_row.is_sample,
            )
            for case_row in visible_cases
        ],
        wording_rules=problem.wording_rules if reveal_hidden else None,
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


def _gradebook_effective_score(submission: Submission) -> float:
    """Score one attempt counts for in the gradebook.

    A teacher's manual mark is the authoritative verdict for an attempt, so it
    wins over the judge's pass percentage - including when the teacher awarded
    ``0.0``, which is a decision and not a missing value.
    """
    return submission.teacher_score if submission.teacher_score is not None else submission.score


#: Leading characters that turn a spreadsheet cell into a formula. Everything
#: below is cell text a *student* (full name, e-mail) or a problem title controls,
#: and the file is opened by a teacher on their own machine.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> str:
    """Render one free-text cell so a spreadsheet cannot execute it as a formula.

    ``csv.writer`` already quotes delimiters and newlines; it does nothing about
    ``=cmd|...`` or ``+1-5555...``, which Excel and LibreOffice happily evaluate
    on open (CSV injection). A leading apostrophe marks the cell as text and is
    the conventional, lossless neutralisation.
    """
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_CSV_FORMULA_PREFIXES) else text


@app.get(
    "/api/classrooms/{classroom_id}/gradebook.csv",
    response_class=Response,
    summary="Class gradebook as a CSV export",
)
async def classroom_gradebook_csv(
    classroom_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> Response:
    """Export one class's gradebook as a spreadsheet-ready CSV download.

    One row per enrolled student, two columns per problem published to the class
    (``<code>_<title>_score`` and ``<code>_attempts``) plus ``average_score`` and
    ``total_score`` at the end. The score column carries the student's **best**
    attempt for that problem: ``teacher_score`` whenever a teacher graded the
    attempt (their mark is the verdict, even when it is lower than the judge's),
    otherwise the judge's ``score``; among equal marks the newest attempt wins -
    while ``attempts`` counts every recorded submission, so a student who ground
    through ten wrong answers is still visible.

    ``total_score`` sums those best scores over the problems published to the
    class (an unattempted problem contributes ``0.0``) and ``average_score``
    divides it by the number of problems, so the two columns always agree.

    The body is UTF-8 **with a BOM**: the sheet is full of Chinese problem
    titles, and Excel on Windows only decodes a BOM-less file as UTF-8 by
    accident. Staff only - the export names every student in the class with their
    e-mail address and their marks, so it is explicitly ``no-store``: a graded
    roster of minors must not sit in any browser or proxy cache.

    Every free-text cell goes through :func:`_csv_cell`, because names, e-mail
    addresses and problem titles are attacker-writable and a teacher opens this
    file in a spreadsheet.
    """
    classroom = _load_classroom(db, classroom_id)
    if not user.is_admin and classroom.teacher_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not teach that class.",
        )

    students = (
        db.query(User)
        .join(ClassroomMembership, ClassroomMembership.student_id == User.id)
        .filter(ClassroomMembership.classroom_id == classroom.id)
        .order_by(User.id)
        .all()
    )
    problems = (
        db.query(Problem)
        .filter(Problem.classroom_id == classroom.id)
        .order_by(Problem.id)
        .all()
    )

    #: (student_id, problem_id) -> (best score, attempt count)
    grades: dict[tuple[int, int], tuple[float, int]] = {}
    if students and problems:
        rows = (
            db.query(Submission)
            .filter(
                Submission.problem_id.in_([problem.id for problem in problems]),
                Submission.student_id.in_([student.id for student in students]),
            )
            # Ascending id: a later attempt replaces an earlier one on a tie, so
            # the newest work is the one that ends up on the sheet.
            .order_by(Submission.id)
            .all()
        )
        for row in rows:
            key = (row.student_id, row.problem_id)
            score = _gradebook_effective_score(row)
            best, attempts = grades.get(key, (0.0, 0))
            grades[key] = (score if score >= best else best, attempts + 1)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["student_id", "username", "full_name", "email"]
    for problem in problems:
        label = problem.problem_code or f"P{problem.id}"
        header.append(_csv_cell(f"{label}_{problem.title}_score"))
        header.append(_csv_cell(f"{label}_attempts"))
    header.extend(["average_score", "total_score"])
    writer.writerow(header)

    problem_ids = [problem.id for problem in problems]
    for student in students:
        line = [
            student.id,
            _csv_cell(student.username),
            _csv_cell(student.full_name),
            _csv_cell(student.email),
        ]
        total = 0.0
        for problem_id in problem_ids:
            best, attempts = grades.get((student.id, problem_id), (0.0, 0))
            total += best
            line.append(round(best, 2))
            line.append(attempts)
        line.append(round(total / len(problem_ids), 2) if problem_ids else 0.0)
        line.append(round(total, 2))
        writer.writerow(line)

    logger.info(
        "staff %s exported the gradebook for class %s (%d students, %d problems)",
        user.id,
        classroom.code,
        len(students),
        len(problems),
    )
    return Response(
        content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=gradebook_{classroom.code}.csv",
            # Personal data of a whole class: never cacheable, never sniffable.
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


@app.get(
    "/api/problems/{problem_id}/pdf",
    response_class=Response,
    summary="Statement PDF of a problem",
)
async def read_problem_pdf(
    problem_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> FileResponse:
    """Stream the A4 statement PDF that came with the problem's pack.

    The file is served ``inline`` so the browser renders it in the tab or PDF
    viewer instead of forcing a download. Authorisation is exactly the problem's
    own visibility rule (:func:`_assert_problem_visible`): a student who joined
    the class may read the statement, a caller who may not open the problem may
    not read the PDF either.

    ``pdf_path`` is written by the pack importer, never taken from a request, and
    it is only handed out when it names a ``*.pdf`` **regular file** - not a
    symlink. That distinction is the whole point: a pack folder is a path a
    teacher chooses, ``FileResponse`` follows symlinks, and the media type is a
    label rather than a check, so a ``statement.pdf`` symlink would otherwise
    turn this endpoint into an arbitrary file read of anything the server user can
    open. Out-of-store paths are still served (that is how a folder pack works)
    but are logged when ``PROBLEM_PACK_ROOTS`` is configured, because that is
    where a misused teacher account would point.

    A pack without a statement, or a PDF that has since been moved on disk,
    answers ``404`` - never the path, and never a guess about why.
    """
    problem = _load_problem(db, problem_id)
    _assert_problem_visible(problem, user, db)

    missing = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="This problem does not have a statement PDF.",
    )
    pdf_path = (problem.pdf_path or "").strip()
    if not pdf_path or not pdf_path.lower().endswith(".pdf"):
        raise missing

    try:
        # ``follow_symlinks=False`` is deliberate: what must be a regular file is
        # the stored path itself, not whatever it happens to point at.
        inode = os.stat(pdf_path, follow_symlinks=False)
    except OSError:
        raise missing from None
    if stat.S_ISLNK(inode.st_mode) or not stat.S_ISREG(inode.st_mode):
        logger.warning(
            "problem %s: statement PDF is not a regular file; refusing to serve it",
            problem.id,
        )
        raise missing

    try:
        resolved_pdf = Path(pdf_path).resolve()
        in_store = resolved_pdf.is_relative_to(_pack_pdf_dir().resolve())
        in_root = any(
            resolved_pdf == root or root in resolved_pdf.parents
            for root in PACK_ALLOWED_ROOTS
        )
    except OSError:  # pragma: no cover - unreadable parent directory
        in_store = in_root = False
    if PACK_ALLOWED_ROOTS and not (in_store or in_root):
        logger.warning(
            "problem %s: statement PDF is outside the PDF store and PROBLEM_PACK_ROOTS",
            problem.id,
        )

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"problem_{problem.id}.pdf",
        content_disposition_type="inline",
        headers={
            # The bytes came out of a teacher's archive: do not let the browser
            # second-guess the type, and do not let a PDF that carries script
            # reach this site's origin.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# --------------------------------------------------------------------------- #
# Stage 3/4: problem-pack import
# --------------------------------------------------------------------------- #


def _authorised_pack_path(raw: str, teacher: User) -> Path:
    """Resolve a teacher-supplied pack path, honouring ``PROBLEM_PACK_ROOTS``.

    Reading an arbitrary server directory through the importer would turn a
    teacher account into a file browser: a pack folder is a path the caller
    controls, so when the operator configures an allow-list the resolved path has
    to sit inside one of its roots. The resolved path is always logged, because an
    import changes what students are graded on.
    """
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:  # pragma: no cover - host path table dependent
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not resolve that pack path: {exc}",
        ) from exc

    if PACK_ALLOWED_ROOTS and not any(
        resolved == root or root in resolved.parents for root in PACK_ALLOWED_ROOTS
    ):
        logger.warning(
            "teacher %s tried to import a pack from %s, outside PROBLEM_PACK_ROOTS",
            teacher.id,
            resolved,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "That path is outside the directories this server imports packs "
                "from (PROBLEM_PACK_ROOTS). Upload the pack archive instead."
            ),
        )
    return resolved


async def _spool_pack_upload(upload: StarletteUploadFile, destination: Path) -> Path:
    """Stream an uploaded archive to ``destination``, enforcing the size cap.

    The cap is counted while copying rather than trusted from ``Content-Length``:
    that header is caller-supplied, and an archive that lies about its size would
    otherwise fill the disk before the importer ever looked at it.
    """
    target = destination / (Path(upload.filename or "pack.zip").name or "pack.zip")
    written = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(1 << 20):
                written += len(chunk)
                if written > MAX_PACK_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Pack archive exceeds the {MAX_PACK_UPLOAD_BYTES}-byte "
                            "upload limit."
                        ),
                    )
                handle.write(chunk)
    finally:
        await upload.close()
    if not written:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file was empty."
        )
    return target


@app.post(
    "/api/problems/import-pack",
    response_model=ProblemImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Import a CodingPass problem pack (server folder or .zip upload)",
)
async def import_problem_pack(
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ProblemImportResult:
    """Turn a ``Pxxxx_題名`` problem pack into a graded problem.

    Accepts ``{"directory_path": "..."}`` for a pack already on the server, or a
    ``multipart/form-data`` body with the pack archive in a ``file`` part (other
    fields as form values). One URL, two shapes, because a teacher either knows a
    server path or drags a zip onto the page.

    The importer auto-discovers the ``庫題卡`` files, reads the statement PDF for
    the title/difficulty/limits, parses the public sample card, and - when both a
    TeDa generator and a reference solution exist - derives the hidden cases from
    a dual run, so expected outputs come from the teacher's own program rather
    than from a model. Anything the pack could not supply is reported in
    ``warnings`` instead of failing the import; a folder that is not a pack at all
    is a ``400``, never a half-created problem.

    The reference solution is stored (``ref_code``) but never sent to students:
    ``ProblemDetail`` has no field for it, and the plagiarism screen is the only
    consumer.

    Re-importing a ``problem_code`` that already exists for this teacher is
    refused, so an import can never silently rewrite test data students were
    already graded against.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    upload: Optional[StarletteUploadFile] = None
    if content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
        try:
            form = await request.form()
        except Exception as exc:  # malformed multipart framing
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not read the multipart form: {exc}",
            ) from exc
        # ``Request.form()`` hands back Starlette's own ``UploadFile``; FastAPI's
        # ``UploadFile`` is a subclass of it, so checking against the FastAPI name
        # would reject every real upload.
        candidate = form.get("file")
        upload = candidate if isinstance(candidate, StarletteUploadFile) else None
        fields = {
            key: value
            for key, value in (
                ("directory_path", form.get("directory_path")),
                ("classroom_id", form.get("classroom_id")),
                ("generate_hidden", form.get("generate_hidden")),
                ("num_random", form.get("num_random")),
                ("seed", form.get("seed")),
            )
            if value not in (None, "")
        }
        try:
            payload = ImportPackRequest(**fields)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=json.loads(exc.json()),
            ) from exc
    else:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expected a JSON body or a multipart/form-data pack upload.",
            ) from exc
        try:
            payload = ImportPackRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=json.loads(exc.json()),
            ) from exc

    # Exactly one source: an upload *and* a path would leave the teacher guessing
    # which one produced the problem in front of them.
    if (payload.directory_path is None) == (upload is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Supply exactly one of `directory_path` (a pack folder or .zip on "
                "the server) or a `file` upload of the pack archive."
            ),
        )

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

    temp_dir: Optional[Path] = None
    try:
        if upload is not None:
            temp_dir = Path(tempfile.mkdtemp(prefix="cj-upload-"))
            source = await _spool_pack_upload(upload, temp_dir)
        else:
            source = _authorised_pack_path(str(payload.directory_path), teacher)
            if not source.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No problem pack found at {source}.",
                )

        try:
            pack = await run_in_threadpool(
                parse_problem_pack,
                str(source),
                # A zip is unpacked into a directory that is deleted immediately
                # afterwards, so the statement PDF must be copied out of it.
                pdf_dir=str(_pack_pdf_dir()),
                generate_hidden=payload.generate_hidden,
                num_random=payload.num_random,
                seed=payload.seed,
            )
        except ProblemPackError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"That is not a usable problem pack: {exc}",
            ) from exc

        problem_code = pack.get("problem_code")
        warnings = [str(note) for note in pack.get("warnings") or []]
        if problem_code:
            clash = (
                db.query(Problem)
                .filter(Problem.problem_code == problem_code)
                .filter(Problem.teacher_id == teacher.id)
                .first()
            )
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{problem_code} is already problem {clash.id} in your "
                        "bank; delete it before re-importing the pack."
                    ),
                )

        cases = list(pack.get("test_cases") or [])
        if len(cases) > MAX_TEST_CASES:
            kept_samples = [case for case in cases if case["is_sample"]][:MAX_TEST_CASES]
            room = max(0, MAX_TEST_CASES - len(kept_samples))
            cases = kept_samples + [
                case for case in cases if not case["is_sample"]
            ][:room]
            warnings.append(
                f"the pack produced more than {MAX_TEST_CASES} cases; kept every "
                f"sample and the first {room} random ones"
            )
        if not cases:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "The pack produced no test cases, so nothing could be graded"
                    + (": " + "; ".join(warnings) if warnings else ".")
                ),
            )

        difficulty = str(pack.get("difficulty") or Difficulty.EASY)
        starter_c = str(pack.get("starter_code_c") or "")
        starter_cpp = str(pack.get("starter_code_cpp") or "")
        problem = Problem(
            title=str(pack.get("title") or pack.get("pack_name") or "Untitled")[:200],
            description=str(pack.get("description") or "")[:MAX_DESCRIPTION_CHARS],
            difficulty=difficulty if difficulty in Difficulty.ALL else Difficulty.EASY,
            time_limit_sec=min(
                max(float(pack.get("time_limit_sec") or 3.0), MIN_TIME_LIMIT_SEC),
                MAX_TIME_LIMIT_SEC,
            ),
            memory_limit_mb=min(
                max(int(pack.get("memory_limit_mb") or 256), MIN_MEMORY_LIMIT_MB),
                MAX_MEMORY_LIMIT_MB,
            ),
            teacher_id=teacher.id,
            classroom_id=payload.classroom_id,
            # A pack ships a reference solution, not a scaffold: students start
            # from the platform's blank-but-compiling template instead.
            starter_code_c=starter_c or DEFAULT_STARTER_C,
            starter_code_cpp=starter_cpp or DEFAULT_STARTER_CPP,
            problem_code=problem_code,
            ref_code=pack.get("ref_code"),
            ref_language=pack.get("ref_language"),
            teda_random_script=pack.get("teda_random_script"),
            wording_rules=pack.get("wording_rules"),
            pdf_path=pack.get("pdf_path"),
        )
        problem.test_cases = [
            TestCase(
                input=str(case.get("input") or ""),
                expected_output=str(case.get("expected_output") or ""),
                is_sample=bool(case.get("is_sample")),
            )
            for case in cases
        ]
        db.add(problem)
        try:
            db.commit()
            db.refresh(problem)
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The class disappeared while the pack was being imported.",
            ) from None

        logger.info(
            "teacher %s imported pack %s as problem %s (%d sample + %d hidden cases)",
            teacher.id,
            pack.get("pack_name"),
            problem.id,
            len(problem.sample_test_cases),
            len(problem.hidden_test_cases),
        )
        return ProblemImportResult(
            **_problem_detail(problem, reveal_hidden=True).model_dump(),
            pack_name=str(pack.get("pack_name") or ""),
            pack_source=str((pack.get("source") or {}).get("kind") or ""),
            sample_case_count=len(problem.sample_test_cases),
            random_case_count=len(problem.hidden_test_cases),
            warnings=warnings,
        )
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


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

    Three extra layers run on top of the pass/fail verdict, all of them recorded
    on the submission and returned in the response:

    ``rubric``
        The 100-point teaching rubric from :func:`_build_rubric`, so an attempt
        that only missed the last hidden case is not reported as a zero.
    ``presentation_warning``
        The answer was correct but not byte-strict (trailing spaces / stray blank
        lines): a formatting fault, not a logic fault.
    ``wording_pass`` / ``wording_violations``
        The TeDa-Wording screen (``*_庫題卡碼文限碼``) of
        :func:`wording_rules.check_wording_rules`, when the problem declares any.
    ``patch_probe``
        The Minimal Patch Probe of :func:`patch_probe.probe_minimal_patch`, run
        only when the attempt did not pass everything - and only for students, so
        a teacher previewing a broken draft does not wait for a rescue attempt.
        It reports *which* slips were repairable and how far the repaired program
        got; it never returns the repaired source.

    The whole run holds one of the ``MAX_CONCURRENT_RUNS`` judge slots, so the
    wall-clock cost (``compile + test_cases x time_limit``: the source is built
    once and the binary is re-executed per case) is bounded and
    shared fairly with ``POST /api/run``. The probe takes a slot of its own. The
    problem's own ``memory_limit_mb`` - not the server default - is the
    ``RLIMIT_AS`` ceiling the submission runs under.
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
    # The advertised per-problem budget is the enforced one: ``RLIMIT_AS`` for the
    # submission is this number (clamped by the sandbox), not the global default.
    memory_limit_mb = int(problem.memory_limit_mb)
    async with _execution_slots(request):
        report = await run_in_threadpool(
            _judge_all_cases,
            payload.code,
            payload.language,
            cases,
            timeout,
            memory_limit_mb,
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

    # -- Stage 4: TeDa-Wording screen ---------------------------------------- #
    # Checked on the raw source: a banned identifier has to be findable where the
    # student wrote it. A problem with no rule file passes trivially, so "no
    # rules declared" can never be mistaken for "the student complied".
    wording_pass = True
    wording_violations: list[str] = []
    if problem.has_wording_rules:
        wording = check_wording_rules(payload.code, problem.wording_rules)
        wording_pass = bool(wording.get("wording_pass", True))
        wording_violations = [str(item) for item in wording.get("violations") or []]

    # -- Stage 4: presentation warning --------------------------------------- #
    # Only an otherwise-correct submission can be a presentation error: a wrong
    # answer is reported as such, and the whitespace noise is not the lesson.
    presentation_warning = bool(
        verdict == SubmissionStatus.ACCEPTED and report.presentation_warning()
    )

    # -- Stage 4: Minimal Patch Probe ---------------------------------------- #
    probe_result: Optional[dict] = None
    if verdict != SubmissionStatus.ACCEPTED and not trial_run:
        probe_cases = _probe_cases(cases)
        if probe_cases:
            async with _execution_slots(request):
                probe_result = await run_in_threadpool(
                    probe_minimal_patch,
                    payload.code,
                    payload.language,
                    probe_cases,
                    timeout,
                )

    rubric = _build_rubric(
        report,
        verdict,
        presentation_warning=presentation_warning,
        wording_pass=wording_pass,
        probe=probe_result,
        trial_run=trial_run,
    )
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
            compile_score=rubric.compile_score,
            sample_score=rubric.sample_score,
            random_score=rubric.random_score,
            logic_score=rubric.logic_score,
            quality_score=rubric.quality_score,
            presentation_warning=presentation_warning,
            wording_pass=wording_pass,
            wording_violations=_wording_json(wording_violations),
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
        "graded problem %s for %s: status=%s score=%.2f (%d/%d) rubric=%.2f "
        "pe=%s wording_pass=%s probe=%s%s",
        problem_id,
        user.username,
        verdict,
        score,
        passed_count,
        total_count,
        rubric.total,
        presentation_warning,
        wording_pass,
        (probe_result or {}).get("applied_fixes") or "-",
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
        rubric=RubricScores(**rubric.as_dict()),
        presentation_warning=presentation_warning,
        wording_pass=wording_pass,
        wording_violations=wording_violations,
        patch_probe=_probe_public(probe_result),
    )


# --------------------------------------------------------------------------- #
# Submission history
# --------------------------------------------------------------------------- #


def _load_submission(db: Session, submission_id: int) -> Submission:
    """Load one submission with its problem and author preloaded, or ``404``."""
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
    return submission


def _assert_submission_visible(db: Session, submission: Submission, user: User) -> None:
    """Raise ``403`` unless ``user`` may read ``submission``.

    Allowed: the author, the problem's teacher, the teacher of the class the
    problem belongs to, and admins.
    """
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
            ai_feedback=row.ai_feedback,
            ai_suggested_score=row.ai_suggested_score,
            ai_model=row.ai_model,
            ai_reviewed_at=row.ai_reviewed_at,
            teacher_feedback=row.teacher_feedback,
            teacher_score=row.teacher_score,
            reviewed_by_id=row.reviewed_by_id,
            reviewed_at=row.reviewed_at,
            rubric_total=row.rubric_total,
        )
        for row in rows
    ]
    return SubmissionListResponse(total=int(total), count=len(payloads), submissions=payloads)


def _submission_detail(submission: Submission) -> SubmissionDetailOut:
    """Serialise one submission (with its source) for the review endpoints."""
    problem = submission.problem
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
        ai_feedback=submission.ai_feedback,
        ai_suggested_score=submission.ai_suggested_score,
        ai_model=submission.ai_model,
        ai_reviewed_at=submission.ai_reviewed_at,
        teacher_feedback=submission.teacher_feedback,
        teacher_score=submission.teacher_score,
        reviewed_by_id=submission.reviewed_by_id,
        reviewed_at=submission.reviewed_at,
        rubric_total=submission.rubric_total,
        rubric=RubricScores(
            compile_score=submission.compile_score,
            sample_score=submission.sample_score,
            random_score=submission.random_score,
            logic_score=submission.logic_score,
            quality_score=submission.quality_score,
            total=submission.rubric_total,
        ),
        presentation_warning=bool(submission.presentation_warning),
        wording_pass=bool(submission.wording_pass),
        wording_violations=_wording_list(submission.wording_violations),
    )


@app.get("/api/submissions/{submission_id}", response_model=SubmissionDetailOut, summary="One submission with its source")
async def read_submission(
    submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SubmissionDetailOut:
    """A single recorded attempt, including the submitted source code.

    Restricted to the author, the problem's teacher and admins - the source is
    the student's own work and is not exposed to other students.
    """
    submission = _load_submission(db, submission_id)
    _assert_submission_visible(db, submission, user)

    return _submission_detail(submission)


# --------------------------------------------------------------------------- #
# Phase 4: AI tutor and teacher review
# --------------------------------------------------------------------------- #


@app.get(
    "/api/ai/status",
    response_model=AIStatusResponse,
    summary="Is the AI tutor reachable?",
)
async def ai_status() -> AIStatusResponse:
    """Report whether the vLLM tutor answers, or whether the heuristic will.

    The probe performs one short HTTP request, so it runs in the threadpool and
    never stalls the event loop while a student waits for the AI panel.
    """
    report = await run_in_threadpool(check_ai_status)
    return AIStatusResponse(**report)


@app.post(
    "/api/ai/analyze",
    response_model=AIAnalyzeResponse,
    summary="Review a source file without grading it",
)
async def ai_analyze(
    payload: AIAnalyzeRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> AIAnalyzeResponse:
    """Return a structured review of ``payload.code`` without storing anything.

    Used by the Run panel and by teachers previewing a solution. Authentication
    is optional, but naming a ``problem_id`` requires it so the statement of a
    class problem cannot be pulled by an anonymous caller. **Test cases -
    sample or hidden - are never sent to the model.**
    """
    title = ""
    description = ""
    if payload.problem_id is not None:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in to review code against a problem.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        problem = _load_problem(db, payload.problem_id)
        _assert_problem_visible(problem, user, db)
        title, description = problem.title, problem.description

    for_teacher = bool(user and (user.is_teacher or user.is_admin))
    analysis = await run_in_threadpool(
        analyze_code,
        payload.code,
        payload.language,
        status=payload.status or "unknown",
        compile_output=payload.compile_output or "",
        stderr=payload.stderr or "",
        problem_title=title,
        problem_description=description,
        for_teacher=for_teacher,
    )
    return AIAnalyzeResponse(**analysis)


@app.post(
    "/api/submissions/{submission_id}/ai-review",
    response_model=SubmissionAIReviewResponse,
    summary="AI review of a recorded submission",
)
async def ai_review_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SubmissionAIReviewResponse:
    """Review one recorded attempt and persist the feedback on its row.

    Visible to the same audience as ``GET /api/submissions/{id}``; the persona
    (coach vs. grader) follows the caller, so only a teacher sees a worked
    solution. A model outage degrades to the heuristic reviewer instead of
    failing the request.
    """
    submission = _load_submission(db, submission_id)
    _assert_submission_visible(db, submission, user)

    problem = submission.problem
    analysis = await run_in_threadpool(
        analyze_code,
        submission.code,
        submission.language,
        status=submission.status,
        problem_title=problem.title if problem else "",
        problem_description=problem.description if problem else "",
        score=submission.score,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        for_teacher=bool(user.is_teacher or user.is_admin),
    )

    submission.ai_feedback = analysis["feedback_markdown"]
    submission.ai_suggested_score = analysis["suggested_score"]
    submission.ai_model = analysis["model"]
    submission.ai_reviewed_at = utcnow()
    db.commit()
    db.refresh(submission)
    logger.info(
        "ai review stored for submission %s (model=%s ai_powered=%s)",
        submission.id,
        analysis["model"],
        analysis["ai_powered"],
    )
    detail = _submission_detail(submission)
    return SubmissionAIReviewResponse(
        **detail.model_dump(), analysis=AIAnalyzeResponse(**analysis)
    )


@app.patch(
    "/api/submissions/{submission_id}/review",
    response_model=SubmissionDetailOut,
    summary="Teacher feedback and score override",
)
async def teacher_review_submission(
    submission_id: int,
    payload: TeacherReviewRequest,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> SubmissionDetailOut:
    """Store manual grading for one attempt.

    Only the problem's own teacher, the teacher of the class it belongs to, or
    an admin may grade. The judge's ``score`` is left untouched; ``teacher_score``
    is the human override shown next to it.
    """
    submission = _load_submission(db, submission_id)
    problem = submission.problem
    allowed = teacher.is_admin or (
        problem is not None
        and (
            problem.teacher_id == teacher.id
            or (
                problem.classroom_id is not None
                and problem.classroom_id in _teacher_classroom_ids(db, teacher)
            )
        )
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may not grade submissions for this problem.",
        )

    submission.teacher_score = payload.teacher_score
    submission.teacher_feedback = (payload.teacher_feedback or "").strip() or None
    submission.reviewed_by_id = teacher.id
    submission.reviewed_at = utcnow()
    db.commit()
    db.refresh(submission)
    logger.info(
        "teacher %s graded submission %s (score=%s)",
        teacher.id,
        submission.id,
        submission.teacher_score,
    )
    return _submission_detail(submission)


# --------------------------------------------------------------------------- #
# Stage 3: plagiarism screen
# --------------------------------------------------------------------------- #


def _plagiarism_label(submission: Submission) -> str:
    """Report label for one submission: recognisable, and unique per student.

    The screen keys its pairs by these labels and never parses them, so the only
    requirements are that a teacher recognises them and that two students cannot
    collide - display names can repeat, ``@username`` cannot.
    """
    student = submission.student
    if student is None:  # pragma: no cover - cascade delete keeps this impossible
        return f"submission {submission.id}"
    return f"{student.display_name} (@{student.username})"


def _is_scaffolding(code: str, problem: Problem, language: str) -> bool:
    """True when the source is still the untouched starter template.

    Comparing two students who never edited the scaffold reports a 100% match
    between two people who did nothing, which is not a finding.
    """
    stripped = (code or "").strip()
    if not stripped:
        return True
    templates = {
        problem.starter_for(language).strip(),
        DEFAULT_STARTER_C.strip(),
        DEFAULT_STARTER_CPP.strip(),
    }
    return stripped in templates


@app.get(
    "/api/classrooms/{classroom_id}/problems/{problem_id}/plagiarism",
    response_model=PlagiarismReport,
    summary="Structural similarity screen for a class problem",
)
async def classroom_problem_plagiarism(
    classroom_id: int,
    problem_id: int,
    threshold: Annotated[
        float, Query(ge=0.0, le=1.0, description="Escalation cut-off for a pair.")
    ] = PLAGIARISM_DEFAULT_THRESHOLD,
    language: Optional[LanguageLiteral] = Query(
        default=None, description="Screen one language only; the default screens both."
    ),
    include_exempted: Annotated[
        bool,
        Query(
            description=(
                "Include threshold hits that Reference Baseline Subtraction "
                "cleared, so the report shows what was dismissed and why."
            )
        ),
    ] = True,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> PlagiarismReport:
    """Rank the suspicious code pairs submitted to one problem of a class.

    The comparison is structural, not textual: :mod:`plagiarism` strips comments,
    strings and includes, rewrites ``for`` as ``while``, and renames every
    non-standard identifier positionally, so renaming ``count`` to ``cnt`` and
    reordering statements does not hide a shared solution. When the problem
    carries a reference solution, two students who both simply wrote the model
    answer are **exempted** (``flagged=False`` with an ``exemption`` note) instead
    of accused - that subtraction is what keeps a one-line exercise from flagging
    the whole class.

    Only the class's own teacher (or an admin) may run this: it discloses other
    students' work to whoever runs it. Submissions are deduplicated to each
    student's strongest attempt per language, and sources that are still
    scaffolding are left out.
    """
    classroom = _load_classroom(db, classroom_id)
    _assert_classroom_visible(classroom, teacher)
    problem = _load_problem(db, problem_id)
    if problem.classroom_id != classroom.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This problem is not part of that class.",
        )
    if not _can_manage_problem(problem, teacher):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the teacher who owns a problem may screen it for plagiarism.",
        )

    member_ids = {membership.student_id for membership in classroom.memberships}
    filters = [Submission.problem_id == problem.id]
    if member_ids:
        filters.append(Submission.student_id.in_(member_ids))
    if language:
        filters.append(Submission.language == language)
    rows = (
        db.query(Submission)
        .options(joinedload(Submission.student))
        .filter(*filters)
        .order_by(Submission.score.desc(), Submission.created_at.desc(), Submission.id.desc())
        .all()
    )

    # Strongest attempt per (student, language): three half-finished drafts of the
    # same student are not three suspects, and they would match each other.
    strongest: dict[tuple[int, str], Submission] = {}
    skipped_trivial = 0
    for row in rows:
        key = (row.student_id, row.language)
        if key in strongest:
            continue
        if _is_scaffolding(row.code, problem, row.language):
            skipped_trivial += 1
            continue
        strongest[key] = row

    selected = sorted(strongest.values(), key=lambda row: row.id)[:PLAGIARISM_MAX_SUBMISSIONS]
    truncated = len(strongest) > len(selected)

    groups: dict[str, dict[str, int]] = {}
    codes: dict[str, str] = {}
    for row in selected:
        label = _plagiarism_label(row)
        codes[label] = row.code or ""
        groups.setdefault(row.language, {})[label] = row.id

    # Different languages are never compared: a C and a C++ solution of the same
    # exercise look alike because the exercise is small, not because anybody
    # copied.
    pairs: list[PlagiarismPair] = []
    reference_available = bool(problem.ref_code and problem.ref_code.strip())
    for group_language, label_to_id in sorted(groups.items()):
        if len(label_to_id) < 2:
            continue
        entries = {label: codes[label] for label in label_to_id}
        measured = await run_in_threadpool(
            compute_similarity_pairs, entries, problem.ref_code or "", threshold
        )
        for entry in measured:
            flagged = bool(entry.get("flagged"))
            if not flagged and not include_exempted:
                continue
            label_a = str(entry["student_a"])
            label_b = str(entry["student_b"])
            pairs.append(
                PlagiarismPair(
                    student_a=label_a,
                    student_b=label_b,
                    submission_id_a=label_to_id[label_a],
                    submission_id_b=label_to_id[label_b],
                    language=group_language,
                    combined_similarity=float(entry["combined_similarity"]),
                    ast_similarity=float(entry["ast_similarity"]),
                    token_similarity=float(entry["token_similarity"]),
                    ngram_jaccard=float(entry.get("ngram_jaccard") or 0.0),
                    ref_similarity_a=entry.get("ref_similarity_a"),
                    ref_similarity_b=entry.get("ref_similarity_b"),
                    flagged=flagged,
                    exemption=entry.get("exemption"),
                )
            )

    pairs.sort(key=lambda pair: pair.combined_similarity, reverse=True)
    flagged_count = sum(1 for pair in pairs if pair.flagged)
    logger.info(
        "plagiarism screen: teacher %s problem %s threshold=%.2f compared=%d "
        "pairs=%d flagged=%d exempt=%d",
        teacher.id,
        problem.id,
        threshold,
        len(selected),
        len(pairs),
        flagged_count,
        len(pairs) - flagged_count,
    )
    return PlagiarismReport(
        problem_id=problem.id,
        problem_title=problem.title,
        problem_code=problem.problem_code,
        classroom_id=classroom.id,
        classroom_name=classroom.name,
        threshold=threshold,
        reference_available=reference_available,
        languages=sorted(groups),
        submissions_compared=len(selected),
        students_compared=len({row.student_id for row in selected}),
        pair_count=len(pairs),
        flagged_count=flagged_count,
        exempt_count=len(pairs) - flagged_count,
        skipped_trivial=skipped_trivial,
        truncated=truncated,
        pairs=pairs,
    )


if __name__ == "__main__":  # pragma: no cover - developer convenience
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8010")),
        reload=bool(os.environ.get("RELOAD", "")),
    )
