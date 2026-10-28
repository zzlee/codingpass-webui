"""FastAPI application for the CodingPass C/C++ judge, classroom and exam server.

This module serves as the primary application entrypoint, wiring routers,
lifespan startup/shutdown, middleware, service health probes, and runtime configuration.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

try:
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
    from .config import (
        DEFAULT_PAGE_SIZE,
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        INVITE_CODE_ALPHABET,
        INVITE_CODE_LENGTH,
        MAX_CONCURRENT_RUNS,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_PACK_UPLOAD_BYTES,
        MAX_PAGE_SIZE,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        PACK_ALLOWED_ROOTS,
        PLAGIARISM_DEFAULT_THRESHOLD,
        PLAGIARISM_MAX_SUBMISSIONS,
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        RUBRIC_SAMPLE_POINTS,
        VERDICT_DETAIL_CHARS,
        _cors_origins,
        _csv_cell,
        _pack_allowed_roots,
        _pack_pdf_dir,
        get_pack_allowed_roots,
    )
    from .database import init_db, utcnow
    from .dependencies import (
        AdminOnly,
        StudentOnly,
        TeacherOnly,
        _assert_classroom_visible,
        _assert_problem_visible,
        _assert_submission_visible,
        _can_manage_problem,
        _classroom_counts,
        _classroom_detail,
        _classroom_out,
        _execution_slots,
        _generate_class_code,
        _gradebook_effective_score,
        _load_classroom,
        _load_problem,
        _load_submission,
        _problem_conditions,
        _problem_counts,
        _problem_detail,
        _problem_summary,
        _student_classroom_ids,
        _submission_conditions,
        _submission_detail,
        _teacher_classroom_ids,
    )
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
    from .patch_probe import (
        BROKEN_ALGORITHM_SCORE,
        CLEAN_PASS_SCORE,
        MAX_PROBE_RUNS,
        MAX_PROBE_SECONDS,
        PATCHED_PASS_SCORE,
        probe_minimal_patch,
    )
    from .routers.agent import router as agent_router
    from .routers.ai import router as ai_router
    from .routers.auth import router as auth_router
    from .routers.classrooms import _is_scaffolding, router as classrooms_router
    from .routers.problems import router as problems_router
    from .routers.submissions import router as submissions_router
    from .routers.users import router as users_router
    from .routers.exams import router as exams_router
    from .schemas import (
        AIAnalyzeRequest,
        AIAnalyzeResponse,
        AIStatusResponse,
        CaseVerdict,
        ClassroomCreate,
        ClassroomDetail,
        ClassroomJoinRequest,
        ClassroomMemberOut,
        ClassroomOut,
        HealthResponse,
        HiddenSummary,
        ImportPackRequest,
        LoginRequest,
        PasswordChangeRequest,
        PatchProbeReport,
        PlagiarismPair,
        PlagiarismReport,
        ProblemCreate,
        ProblemDetail,
        ProblemImportResult,
        ProblemListResponse,
        ProblemSummary,
        RegisterRequest,
        RubricScores,
        RunRequest,
        RunResponse,
        SubmissionAIReviewResponse,
        SubmissionCreate,
        SubmissionDetailOut,
        SubmissionListResponse,
        SubmissionOut,
        SubmissionResult,
        TeacherReviewRequest,
        TestCaseIn,
        TestCasePublic,
        TokenResponse,
        UserListResponse,
        UserOut,
        UserUpdate,
    )
    from .services.judge import (
        CaseOutcome,
        JudgeReport,
        Rubric,
        _build_rubric,
        _case_verdict,
        _clip,
        _judge_all_cases,
        _normalize_output,
        _probe_cases,
        _probe_public,
        _scale,
        _wording_json,
        _wording_list,
        passed_hidden,
    )
    from .ai_tutor import (
        AI_PROVIDER,
        HEURISTIC_MODEL_NAME,
        VLLM_BASE_URL,
        VLLM_MODEL,
        analyze_code,
        check_ai_status,
    )
except ImportError:
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
    from config import (  # type: ignore[no-redef]
        DEFAULT_PAGE_SIZE,
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        INVITE_CODE_ALPHABET,
        INVITE_CODE_LENGTH,
        MAX_CONCURRENT_RUNS,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_PACK_UPLOAD_BYTES,
        MAX_PAGE_SIZE,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        PACK_ALLOWED_ROOTS,
        PLAGIARISM_DEFAULT_THRESHOLD,
        PLAGIARISM_MAX_SUBMISSIONS,
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        RUBRIC_SAMPLE_POINTS,
        VERDICT_DETAIL_CHARS,
        _cors_origins,
        _csv_cell,
        _pack_allowed_roots,
        _pack_pdf_dir,
        get_pack_allowed_roots,
    )
    from database import init_db, utcnow  # type: ignore[no-redef]
    from dependencies import (  # type: ignore[no-redef]
        AdminOnly,
        StudentOnly,
        TeacherOnly,
        _assert_classroom_visible,
        _assert_problem_visible,
        _assert_submission_visible,
        _can_manage_problem,
        _classroom_counts,
        _classroom_detail,
        _classroom_out,
        _execution_slots,
        _generate_class_code,
        _gradebook_effective_score,
        _load_classroom,
        _load_problem,
        _load_submission,
        _problem_conditions,
        _problem_counts,
        _problem_detail,
        _problem_summary,
        _student_classroom_ids,
        _submission_conditions,
        _submission_detail,
        _teacher_classroom_ids,
    )
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
    from patch_probe import (  # type: ignore[no-redef]
        BROKEN_ALGORITHM_SCORE,
        CLEAN_PASS_SCORE,
        MAX_PROBE_RUNS,
        MAX_PROBE_SECONDS,
        PATCHED_PASS_SCORE,
        probe_minimal_patch,
    )
    from routers.agent import router as agent_router  # type: ignore[no-redef]
    from routers.ai import router as ai_router  # type: ignore[no-redef]
    from routers.auth import router as auth_router  # type: ignore[no-redef]
    from routers.classrooms import _is_scaffolding, router as classrooms_router  # type: ignore[no-redef]
    from routers.problems import router as problems_router  # type: ignore[no-redef]
    from routers.submissions import router as submissions_router  # type: ignore[no-redef]
    from routers.users import router as users_router  # type: ignore[no-redef]
    from routers.exams import router as exams_router  # type: ignore[no-redef]
    from schemas import (  # type: ignore[no-redef]
        AIAnalyzeRequest,
        AIAnalyzeResponse,
        AIStatusResponse,
        CaseVerdict,
        ClassroomCreate,
        ClassroomDetail,
        ClassroomJoinRequest,
        ClassroomMemberOut,
        ClassroomOut,
        HealthResponse,
        HiddenSummary,
        ImportPackRequest,
        LoginRequest,
        PasswordChangeRequest,
        PatchProbeReport,
        PlagiarismPair,
        PlagiarismReport,
        ProblemCreate,
        ProblemDetail,
        ProblemImportResult,
        ProblemListResponse,
        ProblemSummary,
        RegisterRequest,
        RubricScores,
        RunRequest,
        RunResponse,
        SubmissionAIReviewResponse,
        SubmissionCreate,
        SubmissionDetailOut,
        SubmissionListResponse,
        SubmissionOut,
        SubmissionResult,
        TeacherReviewRequest,
        TestCaseIn,
        TestCasePublic,
        TokenResponse,
        UserListResponse,
        UserOut,
        UserUpdate,
    )
    from services.judge import (  # type: ignore[no-redef]
        CaseOutcome,
        JudgeReport,
        Rubric,
        _build_rubric,
        _case_verdict,
        _clip,
        _judge_all_cases,
        _normalize_output,
        _probe_cases,
        _probe_public,
        _scale,
        _wording_json,
        _wording_list,
        passed_hidden,
    )
    from ai_tutor import (  # type: ignore[no-redef]
        AI_PROVIDER,
        HEURISTIC_MODEL_NAME,
        VLLM_BASE_URL,
        VLLM_MODEL,
        analyze_code,
        check_ai_status,
    )

logging.basicConfig(
    level=os.environ.get("CODE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("codingpass.api")


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
        await run_in_threadpool(init_db)
        app.state.db_ready = True
    except Exception:
        app.state.db_ready = False
        logger.exception(
            "database initialisation failed - /api/health still "
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
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=origins != ["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)


# --------------------------------------------------------------------------- #
# Root service endpoints
# --------------------------------------------------------------------------- #


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
        "pack_roots_restricted": bool(get_pack_allowed_roots()),
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


# --------------------------------------------------------------------------- #
# Include Routers
# --------------------------------------------------------------------------- #

app.include_router(agent_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(classrooms_router)
app.include_router(problems_router)
app.include_router(exams_router)
app.include_router(submissions_router)
app.include_router(ai_router)


@app.api_route(
    "/api/{unmatched_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def api_not_found(unmatched_path: str):
    """Unmatched /api/* calls return 404 JSON instead of falling through to static SPA."""
    raise HTTPException(status_code=404, detail="API endpoint not found.")


# --------------------------------------------------------------------------- #
# Static Frontend Serving (Single Container Production Deployment)
# --------------------------------------------------------------------------- #

frontend_dist = os.environ.get(
    "FRONTEND_DIST",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")),
)
if os.path.isdir(frontend_dist):
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SpaStaticFiles(StaticFiles):
        """Serve frontend static assets with SPA routing and strict path containment."""

        async def get_response(self, path: str, scope):
            if "\x00" in path or ".." in path:
                raise StarletteHTTPException(status_code=400, detail="Invalid path")
            clean_path = path.lstrip("/")
            full_disk_path = os.path.realpath(os.path.join(self.directory, clean_path))
            if not full_disk_path.startswith(os.path.realpath(self.directory)):
                raise StarletteHTTPException(status_code=400, detail="Invalid path")
            try:
                response = await super().get_response(clean_path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404:
                    base_name = os.path.basename(clean_path)
                    if "." not in base_name:
                        response = await super().get_response("index.html", scope)
                        if response.headers.get("content-type", "").startswith("text/html"):
                            response.headers["Cache-Control"] = "no-cache"
                        response.headers["X-Content-Type-Options"] = "nosniff"
                        return response
                raise
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

    app.mount("/", SpaStaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":  # pragma: no cover - developer convenience
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8010")),
        reload=bool(os.environ.get("RELOAD", "")),
    )
