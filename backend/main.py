"""FastAPI application for the CodingPass C/C++ judge.

Endpoints
---------
``GET  /api/health``  liveness probe plus the detected toolchain versions.
``POST /api/run``     compile + execute a submission and return judge output.

Run it from ``backend/`` with::

    uvicorn main:app --reload --host 127.0.0.1 --port 8000

or ``python main.py``. Interactive docs live at ``/docs``.

Configuration (environment)
---------------------------
``CORS_ALLOW_ORIGINS``  comma separated origins, or ``*`` to allow everyone.
``MAX_CONCURRENT_RUNS`` how many submissions may execute at the same time.
See ``compiler.py`` for the sandbox limits (timeout, memory, output size).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
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

logging.basicConfig(
    level=os.environ.get("CODE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("codingpass.api")

#: Largest accepted submission, in characters.
MAX_SOURCE_CHARS = 200_000
#: Largest accepted stdin payload, in characters.
MAX_STDIN_CHARS = 200_000
#: How many submissions may be compiled/executed simultaneously.
MAX_CONCURRENT_RUNS = max(1, int(os.environ.get("MAX_CONCURRENT_RUNS", "4")))

StatusLiteral = Literal[
    "success", "compile_error", "time_limit_exceeded", "runtime_error", "internal_error"
]
LanguageLiteral = Literal["c", "cpp"]


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the execution semaphore for the lifetime of the worker process."""
    app.state.run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    logger.info(
        "judge ready: languages=%s timeout=%.1fs memory=%dMB concurrency=%d",
        supported_languages(),
        DEFAULT_TIMEOUT_SECONDS,
        MEMORY_LIMIT_BYTES // (1024 * 1024),
        MAX_CONCURRENT_RUNS,
    )
    try:
        yield
    finally:
        app.state.run_slots = None


app = FastAPI(
    title="CodingPass Judge",
    description="Compile and execute C / C++ submissions in a resource-limited sandbox.",
    version="1.0.0",
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
    allow_headers=["Content-Type"],
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
    slots: Optional[asyncio.Semaphore] = getattr(request.app.state, "run_slots", None)
    if slots is None:
        slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        request.app.state.run_slots = slots
    async with slots:
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
    }


if __name__ == "__main__":  # pragma: no cover - developer convenience
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8010")),
        reload=bool(os.environ.get("RELOAD", "")),
    )
