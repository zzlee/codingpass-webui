"""AI Tutor status probe, ad-hoc code analysis, and AI problem generation endpoints."""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

try:
    from ..ai_tutor import analyze_code, check_ai_status
    from ..auth import get_db, get_optional_user
    from ..dependencies import TeacherOnly, _assert_problem_visible, _load_problem
    from ..models import User
    from ..schemas import (
        AIAnalyzeRequest,
        AIAnalyzeResponse,
        AIGenerateProblemRequest,
        AIGenerateProblemResponse,
        AIGeneratedPackInfo,
        AIStatusResponse,
    )
    from ..services.ai_problem_generator import (
        AIProblemGenerationError,
        default_workspace_dir,
        generate_problem,
    )
except ImportError:
    from ai_tutor import analyze_code, check_ai_status  # type: ignore[no-redef]
    from auth import get_db, get_optional_user  # type: ignore[no-redef]
    from dependencies import TeacherOnly, _assert_problem_visible, _load_problem  # type: ignore[no-redef]
    from models import User  # type: ignore[no-redef]
    from schemas import (  # type: ignore[no-redef]
        AIAnalyzeRequest,
        AIAnalyzeResponse,
        AIGenerateProblemRequest,
        AIGenerateProblemResponse,
        AIGeneratedPackInfo,
        AIStatusResponse,
    )
    from services.ai_problem_generator import (  # type: ignore[no-redef]
        AIProblemGenerationError,
        default_workspace_dir,
        generate_problem,
    )

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get(
    "/status",
    response_model=AIStatusResponse,
    summary="Is the AI tutor reachable?",
)
async def ai_status() -> AIStatusResponse:
    """Report whether the vLLM tutor answers, or whether the heuristic will."""
    report = await run_in_threadpool(check_ai_status)
    return AIStatusResponse(**report)


@router.post(
    "/analyze",
    response_model=AIAnalyzeResponse,
    summary="Review a source file without grading it",
)
async def ai_analyze(
    payload: AIAnalyzeRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> AIAnalyzeResponse:
    """Return a structured review of payload.code without storing anything."""
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


@router.post(
    "/generate-problem",
    response_model=AIGenerateProblemResponse,
    summary="Generate a complete problem pack via Pi/Qwen3.8 and TeDa skill",
)
async def ai_generate_problem(
    payload: AIGenerateProblemRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(TeacherOnly),
) -> AIGenerateProblemResponse:
    """Generate problem, C++ solution, TeDa generator, wording rules, and pack files."""
    try:
        res = await run_in_threadpool(
            generate_problem,
            payload.prompt,
            db=db,
            teacher=current_user,
            difficulty=payload.difficulty,
            problem_code=payload.problem_code,
            is_exam=payload.is_exam,
            exam_lock_required=payload.exam_lock_required,
            classroom_id=payload.classroom_id,
            auto_import=payload.auto_import,
            num_random=payload.num_random_cases,
            make_pdf=True,
            make_zip=True,
        )
    except AIProblemGenerationError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI problem generation failed: {err}",
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error in problem generation: {err}",
        ) from err

    paths = res.get("paths", {})
    pack_dir = Path(res.get("pack_dir", ""))
    files_generated = [p.name for p in pack_dir.iterdir()] if pack_dir.is_dir() else []
    zip_url = f"/api/ai/packs/{res['problem_code']}/download" if res.get("zip_path") else None

    return AIGenerateProblemResponse(
        problem_code=res["problem_code"],
        title=res["title"],
        problem_id=res.get("problem_id"),
        description=res["description"],
        difficulty=res["difficulty"],
        time_limit_sec=res["time_limit_sec"],
        memory_limit_mb=res["memory_limit_mb"],
        starter_code_cpp=res["starter_code_cpp"],
        starter_code_c=res["starter_code_c"],
        ref_code=res["ref_code"],
        teda_random_script=res["teda_random_script"],
        wording_rules=res["wording_rules"],
        folder_path=str(pack_dir),
        zip_download_url=zip_url,
        files_generated=files_generated,
        sample_cases=res.get("sample_cases", []),
        hidden_case_count=len(res.get("hidden_test_cases", [])),
        dual_run_verified=bool(res.get("verification", {}).get("dual_run_verified")),
        verification_details=res.get("verification", {}),
        warnings=res.get("warnings", []),
    )


@router.get(
    "/packs",
    response_model=list[AIGeneratedPackInfo],
    summary="List generated problem packs in CodingPass directory",
)
async def list_ai_packs(
    current_user: User = Depends(TeacherOnly),
) -> list[AIGeneratedPackInfo]:
    """Scan workspace CodingPass folder and return metadata for all packs."""
    ws = default_workspace_dir()
    if not ws.exists():
        return []

    results: list[AIGeneratedPackInfo] = []
    for entry in sorted(ws.iterdir()):
        if not entry.is_dir():
            continue
        m = re.match(r"^(P\d+)(?:_(.*))?$", entry.name)
        if not m:
            continue
        p_code = m.group(1)
        title = m.group(2) or p_code

        files = list(entry.glob("*"))
        has_pdf_or_html = any(f.suffix in {".pdf", ".html"} for f in files)
        has_ref = any("碼文標碼" in f.name for f in files)
        has_teda = any("測資０隨輸入" in f.name or "測資0隨輸入" in f.name for f in files)
        has_rules = any("碼文限碼" in f.name for f in files)

        sample_count = 0
        for f in files:
            if "測資範例" in f.name:
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    from ..problem_pack import parse_sample_text
                    samples, _ = parse_sample_text(text)
                    sample_count = len(samples)
                except Exception:
                    pass

        zip_cand = ws / f"{entry.name}.zip"
        zip_path = str(zip_cand) if zip_cand.is_file() else None

        stat = entry.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        results.append(
            AIGeneratedPackInfo(
                problem_code=p_code,
                title=title,
                folder_path=str(entry),
                zip_path=zip_path,
                has_pdf_or_html=has_pdf_or_html,
                has_ref_code=has_ref,
                has_teda=has_teda,
                has_rules=has_rules,
                sample_count=sample_count,
                created_at=created_at,
            )
        )
    return results


@router.get(
    "/packs/{problem_code}/download",
    summary="Download the zipped problem pack",
)
async def download_pack(
    problem_code: str,
    current_user: User = Depends(TeacherOnly),
):
    """Serve the generated .zip problem pack for download."""
    ws = default_workspace_dir()
    if not ws.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Packs folder not found.")

    # Find matching zip or folder
    target_zip: Optional[Path] = None
    target_dir: Optional[Path] = None

    for entry in ws.iterdir():
        if entry.is_file() and entry.name.endswith(".zip") and entry.name.startswith(f"{problem_code}_"):
            target_zip = entry
            break
        if entry.is_dir() and entry.name.startswith(f"{problem_code}_"):
            target_dir = entry

    if target_zip and target_zip.is_file():
        return FileResponse(
            str(target_zip),
            media_type="application/zip",
            filename=target_zip.name,
        )

    if target_dir and target_dir.is_dir():
        # Zip on the fly
        zip_path = ws / f"{target_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(target_dir):
                for f in files:
                    full = Path(root) / f
                    arc = full.relative_to(ws)
                    zf.write(full, arcname=str(arc))
        return FileResponse(
            str(zip_path),
            media_type="application/zip",
            filename=zip_path.name,
        )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pack {problem_code} not found.")
