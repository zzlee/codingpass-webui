"""Exam session management and live anti-cheat proctoring endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    from ..auth import exam_agent_token, get_current_user, get_db, verify_exam_agent_token
    from ..database import utcnow
    from ..dependencies import _assert_problem_visible, _can_manage_problem
    from ..models import ExamSession, Problem, Role, User
    from ..schemas import (
        ExamHeartbeatRequest,
        ExamHeartbeatResponse,
        ExamMonitorResponse,
        ExamSessionOut,
        ExamSessionStartRequest,
    )
except ImportError:
    from auth import exam_agent_token, get_current_user, get_db, verify_exam_agent_token  # type: ignore[no-redef]
    from database import utcnow  # type: ignore[no-redef]
    from dependencies import _assert_problem_visible, _can_manage_problem  # type: ignore[no-redef]
    from models import ExamSession, Problem, Role, User  # type: ignore[no-redef]
    from schemas import (  # type: ignore[no-redef]
        ExamHeartbeatRequest,
        ExamHeartbeatResponse,
        ExamMonitorResponse,
        ExamSessionOut,
        ExamSessionStartRequest,
    )

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/exams", tags=["exams"])


def _session_out(session: ExamSession) -> ExamSessionOut:
    """Format an ExamSession ORM object into ExamSessionOut schema."""
    try:
        violations = json.loads(session.violation_log or "[]")
    except Exception:
        violations = []

    return ExamSessionOut(
        id=session.id,
        problem_id=session.problem_id,
        problem_title=session.problem.title if session.problem else "",
        student_id=session.student_id,
        student_username=session.student.username if session.student else "",
        student_name=session.student.display_name if session.student else "",
        start_time=session.start_time,
        end_time=session.end_time,
        blur_count=session.blur_count,
        fullscreen_exited_count=session.fullscreen_exited_count,
        agent_blocked_count=session.agent_blocked_count,
        agent_lock_verified=session.agent_lock_verified,
        last_heartbeat=session.last_heartbeat,
        status=session.status,
        violations=violations,
    )


@router.post("/{problem_id}/start", response_model=ExamSessionOut, summary="Start or resume an exam session")
async def start_exam_session(
    problem_id: int,
    payload: Optional[ExamSessionStartRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExamSessionOut:
    """Initialize an exam attempt with anti-cheat tracking.
    
    If an in-progress session already exists for this user and problem, it is resumed.
    A session that was already submitted or force-terminated is final: it is never
    resurrected, and restarting returns ``409 Conflict``.
    """
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found.")

    _assert_problem_visible(problem, current_user, db)

    # Exam telemetry endpoints only apply to problems flagged as exams.
    if not problem.is_exam:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This problem is not configured as an exam.",
        )

    # A closed (submitted / terminated) session is immutable: refuse to reset
    # or wipe it so proctoring history can never be laundered for a retake.
    closed = (
        db.query(ExamSession)
        .filter(
            ExamSession.problem_id == problem_id,
            ExamSession.student_id == current_user.id,
            ExamSession.status.in_(("submitted", "terminated")),
        )
        .first()
    )
    if closed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Exam already completed or terminated.",
        )

    # Find existing in-progress session
    session = (
        db.query(ExamSession)
        .filter(
            ExamSession.problem_id == problem_id,
            ExamSession.student_id == current_user.id,
            ExamSession.status == "in_progress",
        )
        .first()
    )

    now = utcnow()
    # The lock token must be a valid HMAC-SHA256 bound to this exact
    # (student, problem) pair; a static or invented string no longer counts.
    agent_locked = bool(
        payload
        and verify_exam_agent_token(payload.agent_lock_token, current_user.id, problem_id)
    )

    if session is not None:
        session.last_heartbeat = now
        if agent_locked:
            session.agent_lock_verified = True
        db.commit()
        db.refresh(session)
        return _session_out(session)

    # Require agent lock if problem enforces it strictly
    if problem.exam_lock_required and not agent_locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This exam requires CodingPass Local Agent with Windows Kiosk Lock enabled.",
        )

    new_session = ExamSession(
        problem_id=problem_id,
        student_id=current_user.id,
        start_time=now,
        last_heartbeat=now,
        agent_lock_verified=agent_locked,
        status="in_progress",
        blur_count=0,
        fullscreen_exited_count=0,
        violation_log="[]",
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    logger.info(
        "started exam session id=%d for student '%s' on problem id=%d",
        new_session.id,
        current_user.username,
        problem_id,
    )
    return _session_out(new_session)


@router.post("/{problem_id}/ticket", summary="Obtain a signed exam lockdown ticket")
async def get_exam_lock_ticket(
    problem_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Issue a cryptographic HMAC ticket for the student's Local Agent."""
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found.")
    _assert_problem_visible(problem, current_user, db)
    if not problem.is_exam:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This problem is not configured as an exam.",
        )
    return {
        "problem_id": problem_id,
        "student_id": current_user.id,
        "ticket": exam_agent_token(current_user.id, problem_id),
    }


@router.post("/{session_id}/heartbeat", response_model=ExamHeartbeatResponse, summary="Send exam anti-cheat heartbeat")
async def exam_heartbeat(
    session_id: int,
    payload: ExamHeartbeatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExamHeartbeatResponse:
    """Receive student telemetry heartbeat (fullscreen state, window blur events).

    Only a live ``in_progress`` session accepts telemetry; submitted or
    terminated sessions are immutable and reject further beats.
    """
    session = db.get(ExamSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam session not found.")

    if session.student_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")

    if session.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Exam session is not in progress (status={session.status}).",
        )

    problem = session.problem
    now = utcnow()
    session.last_heartbeat = now

    if payload.agent_locked:
        session.agent_lock_verified = True

    # Prefer the explicit constrained increment; legacy clients only flip
    # the boolean, which historically counted as one fullscreen exit.
    fullscreen_increment = payload.fullscreen_exited_increment
    if fullscreen_increment == 0 and not payload.is_fullscreen:
        fullscreen_increment = 1
    session.fullscreen_exited_count += fullscreen_increment

    if payload.blur_increment > 0:
        session.blur_count += payload.blur_increment

    # Agent hook telemetry is a cumulative counter: keep the highest seen.
    if payload.agent_blocked_count > session.agent_blocked_count:
        session.agent_blocked_count = payload.agent_blocked_count

    if payload.violation_event:
        try:
            violations = json.loads(session.violation_log or "[]")
        except Exception:
            violations = []
        violations.append({
            "event": payload.violation_event,
            "timestamp": now.isoformat(),
        })
        session.violation_log = json.dumps(violations[-100:])  # Cap last 100 events

    warning = None
    max_blurs = problem.max_blur_count if problem else 3
    if session.blur_count > max_blurs:
        warning = f"Window blurred {session.blur_count} times, exceeding maximum limit ({max_blurs})."

    db.commit()

    return ExamHeartbeatResponse(
        status=session.status,
        blur_count=session.blur_count,
        max_blur_count=max_blurs,
        fullscreen_exited_count=session.fullscreen_exited_count,
        agent_lock_verified=session.agent_lock_verified,
        warning=warning,
    )


@router.get("/{problem_id}/monitor", response_model=ExamMonitorResponse, summary="Teacher live proctoring dashboard")
async def monitor_exam(
    problem_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExamMonitorResponse:
    """View active students, blur counts, and live telemetry for a problem."""
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found.")

    if not _can_manage_problem(problem, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the problem teacher or an admin may access the proctoring monitor.",
        )

    sessions = (
        db.query(ExamSession)
        .filter(ExamSession.problem_id == problem_id)
        .order_by(ExamSession.start_time.desc())
        .all()
    )

    now = utcnow()
    # Active: in progress and heartbeat within 30 seconds
    active_count = 0
    for s in sessions:
        if s.status == "in_progress" and (now - s.last_heartbeat).total_seconds() < 30:
            active_count += 1

    return ExamMonitorResponse(
        problem_id=problem.id,
        problem_title=problem.title,
        is_exam=problem.is_exam,
        exam_lock_required=problem.exam_lock_required,
        max_blur_count=problem.max_blur_count,
        active_students=active_count,
        sessions=[_session_out(s) for s in sessions],
    )


@router.post("/{session_id}/force-submit", summary="Teacher force-submit or terminate exam session")
async def force_submit_exam(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Force close or terminate a student's exam session."""
    session = db.get(ExamSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam session not found.")

    if not _can_manage_problem(session.problem, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")

    session.status = "submitted"
    session.end_time = utcnow()
    db.commit()
    logger.info("teacher '%s' forced submission of exam session id=%d", current_user.username, session.id)
    return {"message": "Exam session successfully closed."}
