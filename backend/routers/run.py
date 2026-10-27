"""Playground / standalone code execution endpoint."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

try:
    from ..compiler import DEFAULT_TIMEOUT_SECONDS, RunResult, run_code
    from ..dependencies import _execution_slots
    from ..schemas import RunRequest, RunResponse
except ImportError:
    from compiler import DEFAULT_TIMEOUT_SECONDS, RunResult, run_code  # type: ignore[no-redef]
    from dependencies import _execution_slots  # type: ignore[no-redef]
    from schemas import RunRequest, RunResponse  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api", tags=["run"])


@router.post("/run", response_model=RunResponse)
async def run_submission(request: Request, payload: RunRequest) -> RunResponse:
    """Compile the submission and run it against stdin."""
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
