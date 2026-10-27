"""Problem bank, problem pack import, and submission execution endpoints."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

try:
    from ..auth import get_current_user, get_db
    from ..config import (
        DEFAULT_PAGE_SIZE,
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        MAX_DESCRIPTION_CHARS,
        MAX_PACK_UPLOAD_BYTES,
        MAX_PAGE_SIZE,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MAX_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        _pack_pdf_dir,
        get_pack_allowed_roots,
    )
    from ..dependencies import (
        TeacherOnly,
        _assert_problem_visible,
        _can_manage_problem,
        _execution_slots,
        _load_problem,
        _problem_conditions,
        _problem_counts,
        _problem_detail,
        _problem_summary,
    )
    from ..models import (
        Classroom,
        Difficulty,
        DifficultyLiteral,
        Problem,
        Submission,
        SubmissionStatus,
        TestCase,
        User,
    )
    from ..patch_probe import probe_minimal_patch
    from ..problem_pack import ProblemPackError, parse_problem_pack
    from ..schemas import (
        HiddenSummary,
        ImportPackRequest,
        ProblemCreate,
        ProblemDetail,
        ProblemImportResult,
        ProblemListResponse,
        RubricScores,
        SubmissionCreate,
        SubmissionResult,
        TestCaseIn,
    )
    from ..services.judge import (
        _build_rubric,
        _case_verdict,
        _judge_all_cases,
        _probe_cases,
        _probe_public,
        _wording_json,
    )
    from ..wording_rules import check_wording_rules
except ImportError:
    from auth import get_current_user, get_db  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        DEFAULT_PAGE_SIZE,
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        MAX_DESCRIPTION_CHARS,
        MAX_PACK_UPLOAD_BYTES,
        MAX_PAGE_SIZE,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MAX_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
        _pack_pdf_dir,
        get_pack_allowed_roots,
    )
    from dependencies import (  # type: ignore[no-redef]
        TeacherOnly,
        _assert_problem_visible,
        _can_manage_problem,
        _execution_slots,
        _load_problem,
        _problem_conditions,
        _problem_counts,
        _problem_detail,
        _problem_summary,
    )
    from models import (  # type: ignore[no-redef]
        Classroom,
        Difficulty,
        DifficultyLiteral,
        Problem,
        Submission,
        SubmissionStatus,
        TestCase,
        User,
    )
    from patch_probe import probe_minimal_patch  # type: ignore[no-redef]
    from problem_pack import ProblemPackError, parse_problem_pack  # type: ignore[no-redef]
    from schemas import (  # type: ignore[no-redef]
        HiddenSummary,
        ImportPackRequest,
        ProblemCreate,
        ProblemDetail,
        ProblemImportResult,
        ProblemListResponse,
        RubricScores,
        SubmissionCreate,
        SubmissionResult,
        TestCaseIn,
    )
    from services.judge import (  # type: ignore[no-redef]
        _build_rubric,
        _case_verdict,
        _judge_all_cases,
        _probe_cases,
        _probe_public,
        _wording_json,
    )
    from wording_rules import check_wording_rules  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.api")

router = APIRouter(prefix="/api/problems", tags=["problems"])


def _authorised_pack_path(raw: str, teacher: User) -> Path:
    """Resolve a teacher-supplied pack path, honouring PROBLEM_PACK_ROOTS."""
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not resolve that pack path: {exc}",
        ) from exc

    allowed_roots = get_pack_allowed_roots()
    if allowed_roots and not any(
        resolved == root or root in resolved.parents for root in allowed_roots
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
    """Stream an uploaded archive to destination, enforcing the size cap."""
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


@router.get("", response_model=ProblemListResponse, summary="List problems")
async def list_problems(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    classroom_id: Optional[int] = Query(default=None, ge=1, description="Filter by class."),
    difficulty: Optional[DifficultyLiteral] = Query(default=None, description="Filter by difficulty."),
    mine: bool = Query(default=False, description="Only problems created by me (teachers)."),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> ProblemListResponse:
    """Problems the caller is entitled to see, newest first."""
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


@router.post(
    "",
    response_model=ProblemDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a problem",
)
async def create_problem(
    payload: ProblemCreate,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ProblemDetail:
    """Create a problem with its sample and hidden test cases."""
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


@router.get(
    "/{problem_id}",
    response_model=ProblemDetail,
    summary="Problem detail (sample cases only)",
)
async def read_problem(
    problem_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemDetail:
    """Statement, limits, starter code and sample test cases."""
    problem = _load_problem(db, problem_id)
    _assert_problem_visible(problem, user, db)
    return _problem_detail(problem, reveal_hidden=_can_manage_problem(problem, user))


@router.post(
    "/{problem_id}/testcases",
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


@router.get(
    "/{problem_id}/pdf",
    response_class=Response,
    summary="Statement PDF of a problem",
)
async def read_problem_pdf(
    problem_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> FileResponse:
    """Stream the A4 statement PDF that came with the problem's pack."""
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
        inode = os.stat(pdf_path, follow_symlinks=False)
    except OSError:
        raise missing from None
    if stat.S_ISLNK(inode.st_mode) or not stat.S_ISREG(inode.st_mode):
        logger.warning(
            "problem %s: statement PDF is not a regular file; refusing to serve it",
            problem.id,
        )
        raise missing

    allowed_roots = get_pack_allowed_roots()
    try:
        resolved_pdf = Path(pdf_path).resolve()
        in_store = resolved_pdf.is_relative_to(_pack_pdf_dir().resolve())
        in_root = any(
            resolved_pdf == root or root in resolved_pdf.parents
            for root in allowed_roots
        )
    except OSError:
        in_store = in_root = False
    if allowed_roots and not (in_store or in_root):
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
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/import-pack",
    response_model=ProblemImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Import a CodingPass problem pack (server folder or .zip upload)",
)
async def import_problem_pack(
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(TeacherOnly),
) -> ProblemImportResult:
    """Turn a Pxxxx_題名 problem pack into a graded problem."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    upload: Optional[StarletteUploadFile] = None
    if content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not read the multipart form: {exc}",
            ) from exc
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


@router.post(
    "/{problem_id}/submit",
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
    """Run code against all test cases of the problem and grade it."""
    problem = _load_problem(db, problem_id)
    _assert_problem_visible(problem, user, db)

    cases = list(problem.test_cases)
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This problem has no test cases yet, so it cannot be graded.",
        )

    timeout = min(max(float(problem.time_limit_sec), MIN_TIME_LIMIT_SEC), MAX_TIME_LIMIT_SEC)
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

    wording_pass = True
    wording_violations: list[str] = []
    if problem.has_wording_rules:
        wording = check_wording_rules(payload.code, problem.wording_rules)
        wording_pass = bool(wording.get("wording_pass", True))
        wording_violations = [str(item) for item in wording.get("violations") or []]

    presentation_warning = bool(
        verdict == SubmissionStatus.ACCEPTED and report.presentation_warning()
    )

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
