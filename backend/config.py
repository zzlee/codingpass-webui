"""Runtime configuration and constants for the CodingPass backend."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Literal

# --- Source and Payload Limits ---------------------------------------------- #
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
#: Test cases per problem.
MAX_TEST_CASES = 50

# --- Per-Problem Limits Bounds ---------------------------------------------- #
MIN_TIME_LIMIT_SEC = 0.1
MAX_TIME_LIMIT_SEC = 20.0
MIN_MEMORY_LIMIT_MB = 16
MAX_MEMORY_LIMIT_MB = 4096

#: Characters of stderr / compiler output echoed back inside one case verdict.
VERDICT_DETAIL_CHARS = 2_000

# --- Pagination & Classroom Codes ------------------------------------------ #
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 6

# --- Problem-Pack Import Configuration -------------------------------------- #
MAX_PACK_UPLOAD_BYTES = max(1024, int(os.environ.get("MAX_PACK_UPLOAD_BYTES", str(64 * 1024 * 1024))))


def _pack_pdf_dir() -> Path:
    """Where a statement PDF extracted from an uploaded archive is persisted."""
    return Path(
        os.environ.get("PROBLEM_PACK_PDF_DIR", "").strip()
        or (Path(__file__).resolve().parent / "problem_packs")
    )


def _pack_allowed_roots() -> list[Path]:
    """Directories a teacher may import a pack *folder* from."""
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
        except OSError:
            pass
    return roots


PACK_ALLOWED_ROOTS: list[Path] = _pack_allowed_roots()


def get_pack_allowed_roots() -> list[Path]:
    """Return active pack allowed roots, honoring test fixtures patching main.PACK_ALLOWED_ROOTS."""
    main_mod = sys.modules.get("main") or sys.modules.get("backend.main")
    if main_mod is not None and hasattr(main_mod, "PACK_ALLOWED_ROOTS"):
        return getattr(main_mod, "PACK_ALLOWED_ROOTS")
    return PACK_ALLOWED_ROOTS


# --- Plagiarism Screen ----------------------------------------------------- #
PLAGIARISM_DEFAULT_THRESHOLD = 0.85
PLAGIARISM_MAX_SUBMISSIONS = max(
    2, int(os.environ.get("PLAGIARISM_MAX_SUBMISSIONS", "200"))
)

# --- 100-Point Teaching Rubric Bands ---------------------------------------- #
RUBRIC_COMPILE_POINTS = 10.0
RUBRIC_SAMPLE_POINTS = 10.0
RUBRIC_RANDOM_POINTS = 30.0
RUBRIC_LOGIC_POINTS = 40.0
RUBRIC_QUALITY_POINTS = 10.0

# --- Starter Codes --------------------------------------------------------- #
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

# --- CSV Sanitisation ------------------------------------------------------- #
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> str:
    """Render one free-text cell so a spreadsheet cannot execute it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_CSV_FORMULA_PREFIXES) else text


# --- Validation Regexes ----------------------------------------------------- #
USERNAME_RE = re.compile(r"[a-z][a-z0-9._-]{2,31}")
EMAIL_RE = re.compile(r"[^@\s;]+@[^@\s;]+\.[^@\s;]+")
INVITE_CODE_RE = re.compile(r"[A-Z0-9_-]{3,16}")

# --- Type Aliases ----------------------------------------------------------- #
StatusLiteral = Literal[
    "success", "compile_error", "time_limit_exceeded", "runtime_error", "internal_error"
]
LanguageLiteral = Literal["c", "cpp"]
CaseStatusLiteral = Literal[
    "accepted", "wrong_answer", "time_limit_exceeded", "runtime_error", "compile_error"
]


# --- CORS ------------------------------------------------------------------ #
def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
    if raw == "*":
        return ["*"]
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]
