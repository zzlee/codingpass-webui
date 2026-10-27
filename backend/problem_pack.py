"""Import CodingPass problem packs (``Pxxxx_題名/`` folders or ``.zip`` bundles).

A *problem pack* follows the offline 庫題卡 conventions established by
``codingpass-tests``:

=============================================================  ====================================
Pack file                                                        Role
=============================================================  ====================================
``P1001_庫題卡碼文標碼.cpp``                                     teacher reference solution
``P1001_庫題卡測資範例.txt``                                    static sample stdin/stdout pairs
``P1001_庫題卡測資０隨輸入.teda``                               TeDa random input generator
``P1001_庫題卡碼文限碼.txt``                                    TeDa wording/structure rules
``P1001_庫題卡測資評判輸出.teda`` (optional)                    custom output comparator (stored verbatim)
``P1001_反轉單字.pdf``                                           A4 problem statement
``P1001_出題設定說明.md``                                        teacher setup notes (becomes the description)
=============================================================  ====================================

:func:`parse_problem_pack` auto-discovers these files, extracts the
``(problem_code, title)`` identity from the pack folder name
(``P1001_反轉單字`` -> ``P1001`` / ``反轉單字``), parses the sample pairs, and -
when *both* the TeDa generator and the reference solution are present -
dynamically builds ``num_random`` hidden test cases by piping 30 generated
random inputs through the compiled reference (see
:func:`teda_engine.generate_random_inputs` + :func:`teda_engine.run_dual_run`).

Example::

    pack = parse_problem_pack("P1001_反轉單字.zip", pdf_dir=UPLOAD_DIR)
    problem = Problem(
        title=pack["title"],
        problem_code=pack["problem_code"],
        description=pack["description"],
        difficulty=pack["difficulty"],
        time_limit_sec=pack["time_limit_sec"],
        memory_limit_mb=pack["memory_limit_mb"],
        ref_code=pack["ref_code"],
        teda_random_script=pack["teda_random_script"],
        wording_rules=pack["wording_rules"],
        pdf_path=pack["pdf_path"],
        teacher_id=teacher.id,
    )
    for case in pack["test_cases"]:
        db.add(TestCase(problem_id=..., **case))

The function is defensive: a missing optional file is a *warning*, never a
crash, and a broken generator/reference still yields the static parts of the
pack (title, description, samples, rules) with the failure recorded in
``result["warnings"]``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, Union

try:  # package-style launch: `uvicorn backend.main:app`
    from . import teda_engine
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    import teda_engine  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.problem_pack")

__all__ = ["parse_problem_pack", "parse_sample_file", "parse_sample_text"]

# --------------------------------------------------------------------------- #
# Pack file conventions
# --------------------------------------------------------------------------- #

REF_GLOB_CPP = "*_庫題卡碼文標碼.cpp"
REF_GLOB_C = "*_庫題卡碼文標碼.c"
SAMPLE_GLOB = "*_庫題卡測資範例.txt"
TEDA_GLOB = "*_庫題卡測資０隨輸入.teda"
RULES_GLOB = "*_庫題卡碼文限碼.txt"
COMPARATOR_GLOB = "*_庫題卡測資評判輸出.teda"
README_GLOB = "*_出題設定說明.md"
PDF_GLOB = "*.pdf"

#: ``P1001_反轉單字`` / ``p1001 - 反轉單字`` -> ("P1001", "反轉單字").
_IDENTITY_RE = re.compile(r"([A-Za-z]{1,4}\d{1,6})[_.\-\s]*(.+)$")

#: Full-width zero in the TeDa generator name occasionally ships half-width.
TEDA_GLOB_FALLBACK = "*_庫題卡測資0隨輸入.teda"


class ProblemPackError(ValueError):
    """The directory/zip does not contain a recognisable problem pack."""


# --------------------------------------------------------------------------- #
# Sample file parsing (*_庫題卡測資範例.txt)
# --------------------------------------------------------------------------- #


def parse_sample_text(content: str) -> List[Tuple[str, str]]:
    """Parse sample-card text supporting both layouts:

    1. Interleaved numbered sections: ``【範例輸入 1】...【範例輸出 1】...``
    2. A single block: ``【範例輸入】...【範例輸出】...`` (multi-case blocks kept
       verbatim, mirroring the offline sandbox behaviour).
    """
    pairs: List[Tuple[str, str]] = []

    numbered_inputs = re.findall(
        r"【範例輸入\s*(\d+)】\s*\n(.*?)(?=【範例輸出\s*\1】)",
        content,
        flags=re.DOTALL,
    )
    numbered_outputs = re.findall(
        r"【範例輸出\s*(\d+)】\s*\n(.*?)(?=【範例輸入|\Z)",
        content,
        flags=re.DOTALL,
    )
    if numbered_inputs and len(numbered_inputs) == len(numbered_outputs):
        out_map = {num: text.strip("\r\n") for num, text in numbered_outputs}
        for num, inp_text in numbered_inputs:
            pairs.append((inp_text.strip("\r\n"), out_map.get(num, "")))
        return pairs

    # Fallback: single 【範例輸入】 / 【範例輸出】 block.
    m = re.search(r"【範例輸入】\s*\n(.*?)【範例輸出】\s*\n(.*)", content, flags=re.DOTALL)
    if m:
        pairs.append((m.group(1).strip("\r\n"), m.group(2).strip("\r\n")))
    return pairs


def parse_sample_file(sample_path: Union[Path, str]) -> List[Tuple[str, str]]:
    """Parse a ``*_庫題卡測資範例.txt`` file into ``[(stdin, expected), ...]``."""
    path = Path(sample_path)
    return parse_sample_text(path.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------------------- #
# Small text normalisation helpers
# --------------------------------------------------------------------------- #


def _ensure_trailing_newline(text: str) -> str:
    """Judge inputs/answers are newline-terminated like the offline pipeline."""
    if text == "":
        return text
    normalised = text.replace("\r\n", "\n")
    if not normalised.endswith("\n"):
        normalised += "\n"
    return normalised


def _read_text(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("problem pack: cannot read %s: %s", path, exc)
        return None


# --------------------------------------------------------------------------- #
# Pack discovery
# --------------------------------------------------------------------------- #


def _first(directory: Path, pattern: str) -> Optional[Path]:
    """First (sorted, deterministic) file matching ``pattern`` in ``directory``."""
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def _has_pack_markers(directory: Path) -> bool:
    return bool(
        _first(directory, REF_GLOB_CPP)
        or _first(directory, REF_GLOB_C)
        or _first(directory, SAMPLE_GLOB)
        or _first(directory, TEDA_GLOB)
        or _first(directory, TEDA_GLOB_FALLBACK)
    )


def _find_pack_dir(base: Path) -> Tuple[Path, List[str]]:
    """Locate the real pack directory under ``base`` (handles zip wrappers).

    Returns ``(pack_dir, warnings)``; when several pack folders exist only the
    first (sorted) one is used and the rest are reported as warnings.
    """
    warnings: List[str] = []
    candidates: List[Path] = []
    if _has_pack_markers(base):
        candidates.append(base)
    try:
        children = sorted(p for p in base.rglob("*") if p.is_dir())
    except OSError:  # pragma: no cover - unreadable tree
        children = []
    for child in children:
        if child != base and _has_pack_markers(child):
            candidates.append(child)

    if not candidates:
        raise ProblemPackError(
            f"no CodingPass pack files (库题卡 標碼/測資/隨輸入) found under {base}"
        )
    if len(candidates) > 1:
        extras = ", ".join(str(c.relative_to(base)) for c in candidates[1:4])
        warnings.append(
            f"{len(candidates)} problem folders found; imported '{candidates[0].name}', "
            f"ignored: {extras}{' ...' if len(candidates) > 4 else ''}"
        )
    return candidates[0], warnings


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract ``zip_path`` into ``dest`` rejecting zip-slip / absolute paths."""
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ProblemPackError(
                    f"refusing to extract unsafe zip member: {member.filename!r}"
                )
            target = dest / name
            # Belt and braces: the normalised target must stay inside dest.
            normalised = os.path.normpath(str(target))
            if os.path.commonpath([str(dest_resolved), normalised]) != str(dest_resolved):
                raise ProblemPackError(
                    f"refusing to extract unsafe zip member: {member.filename!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            is_link = bool(
                member.create_system == 3
                and stat.S_ISLNK(member.external_attr >> 16)
            )
            if is_link:  # never materialise symlinks from an untrusted archive
                raise ProblemPackError(
                    f"refusing to extract symlink zip member: {member.filename!r}"
                )
            with archive.open(member) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)


def _pack_identity(pack_dir: Path, ref_path: Optional[Path]) -> Tuple[Optional[str], str]:
    """Extract ``(problem_code, title)`` from the folder / reference file name."""
    m = _IDENTITY_RE.match(pack_dir.name)
    if m:
        return m.group(1).upper(), m.group(2).strip() or pack_dir.name

    # Folder name unhelpful: fall back to the reference file prefix,
    # e.g. ``P1001_庫題卡碼文標碼.cpp`` -> code "P1001".
    if ref_path is not None:
        stem = ref_path.stem
        code_match = re.match(r"([A-Za-z]{1,4}\d{1,6})(?=[_.\-\s]|$)", stem)
        if code_match:
            return code_match.group(1).upper(), pack_dir.name
    return None, pack_dir.name


# --------------------------------------------------------------------------- #
# Teacher notes (*_出題設定說明.md) metadata extraction
# --------------------------------------------------------------------------- #


def _difficulty_from_readme(text: Optional[str]) -> str:
    if not text:
        return "easy"
    m = re.search(r"(?:難易度|[Dd]ifficulty)[^\n]*", text)
    scope = (m.group(0) if m else "").lower()
    if any(marker in scope for marker in ("高級", "進階", "困難", "hard")):
        return "hard"
    if any(marker in scope for marker in ("中級", "中等", "medium")):
        return "medium"
    return "easy"


def _time_limit_from_readme(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(?:時間限制|[Tt]ime limit)[^\d]*(\d+(?:\.\d+)?)\s*ms", text)
    if not m:
        m = re.search(r"(?:時間限制|[Tt]ime limit)[^\d]*(\d+(?:\.\d+)?)\s*s(?:ec)?\b", text)
        if not m:
            return None
        seconds = float(m.group(1))
    else:
        seconds = float(m.group(1)) / 1000.0
    # Clamp to a sane judge window; the sandbox keeps its own hard ceiling anyway.
    return max(1.0, min(seconds, 30.0))


def _memory_limit_from_readme(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(
        r"(?:空間限制|[Mm]emory limit)[^\d]*(\d+)\s*(kb|mb|gb)", text
    )
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    megabytes = {
        "kb": value / 1024.0,
        "mb": float(value),
        "gb": float(value) * 1024.0,
    }[unit]
    return max(1, int(round(megabytes)))


def _generated_description(code: Optional[str], title: str, sample_count: int, hidden_count: int) -> str:
    """Standard summary used when the pack ships no 出題設定說明.md."""
    heading = f"{code} {title}".strip()
    hidden_note = (
        f"{hidden_count} 筆隱藏測資（由 TeDa 隨機測資腳本搭配標竿正解動態產生）"
        if hidden_count
        else "隱藏測資（本包未附隨機測資腳本，僅以公開範例評測）"
    )
    return (
        f"# {heading}\n"
        "\n"
        "本題目由 CodingPass 題庫卡（庫題卡）自動匯入。\n"
        "\n"
        "### 輸入\n"
        "讀取輸入直到檔案結束（EOF）；每筆測資的精確格式請參閱題本 PDF 與下方範例輸入。\n"
        "\n"
        "### 輸出\n"
        "依序輸出每筆測資的答案，格式需與範例輸出完全一致（比較時忽略行末多餘空白與檔尾空行）。\n"
        "\n"
        "### 說明\n"
        f"* 本題包含 {sample_count} 筆公開範例與 {hidden_note}。\n"
        "* 原始碼需通過語量規則（庫題卡碼文限碼）靜態檢查。\n"
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def parse_problem_pack(
    pack_dir_or_zip: Union[Path, str],
    *,
    pdf_dir: Optional[Union[Path, str]] = None,
    generate_hidden: bool = True,
    seed: int = 42,
    num_random: int = 30,
    time_limit_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Parse a CodingPass problem pack folder or zip into an ORM-ready dict.

    Args:
        pack_dir_or_zip: path to the ``Pxxxx_題名`` folder, or to a ``.zip``
            containing such a folder (or its files at the archive root).
        pdf_dir: where the statement PDF should be *persisted* when the pack is
            a zip (the extraction folder is temporary). Omit to drop the PDF
            and record a warning; for a folder pack ``pdf_path`` simply points
            at the existing file.
        generate_hidden: run the TeDa generator + dual-run reference pipeline
            to build ``num_random`` hidden test cases. Only attempted when both
            the TeDa script and the reference solution exist.
        seed: base seed for the deterministic TeDa PRNG (case *i* uses
            ``seed * 1000 + i``, same as the offline sandbox).
        num_random: how many hidden cases to generate (default 30).
        time_limit_sec: per-case wall-clock limit for the reference binary;
            defaults to the value parsed from the teacher notes, else 3.0s.

    Returns:
        Dict with keys ``problem_code``, ``title``, ``description``,
        ``difficulty``, ``time_limit_sec``, ``memory_limit_mb``,
        ``starter_code_c``, ``starter_code_cpp``, ``ref_code``,
        ``ref_language``, ``teda_random_script``, ``wording_rules``,
        ``output_comparator``, ``pdf_path``, ``sample_test_cases``,
        ``hidden_test_cases``, ``test_cases`` (samples + hidden combined,
        each ``{"input", "expected_output", "is_sample"}``), ``pack_name``,
        ``source`` and ``warnings``.

    Raises:
        ProblemPackError: the path is neither a readable folder nor a readable
            zip, or no pack files were found inside.
    """
    path = Path(pack_dir_or_zip)
    warnings: List[str] = []
    temp_dir: Optional[str] = None
    try:
        # ---- materialise the pack ------------------------------------------ #
        if path.is_dir():
            base = path
            source = {"kind": "dir", "path": str(path.resolve())}
        elif path.is_file() and (path.suffix.lower() == ".zip" or zipfile.is_zipfile(path)):
            temp_dir = tempfile.mkdtemp(prefix="cj-pack-")
            base = Path(temp_dir)
            try:
                _safe_extract_zip(path, base)
            except zipfile.BadZipFile as exc:
                raise ProblemPackError(f"{path} is not a valid zip archive: {exc}") from exc
            source = {"kind": "zip", "path": str(path.resolve())}
        else:
            raise ProblemPackError(f"problem pack not found: {path}")

        pack_dir, dir_warnings = _find_pack_dir(base)
        warnings.extend(dir_warnings)

        # ---- auto-discover the 庫題卡 files ------------------------------- #
        ref_path = _first(pack_dir, REF_GLOB_CPP)
        ref_language = "cpp"
        if ref_path is None:
            ref_path_c = _first(pack_dir, REF_GLOB_C)
            if ref_path_c is not None:
                ref_path, ref_language = ref_path_c, "c"
        sample_path = _first(pack_dir, SAMPLE_GLOB)
        teda_path = _first(pack_dir, TEDA_GLOB) or _first(pack_dir, TEDA_GLOB_FALLBACK)
        rules_path = _first(pack_dir, RULES_GLOB)
        comparator_path = _first(pack_dir, COMPARATOR_GLOB)
        readme_path = _first(pack_dir, README_GLOB)
        pdf_candidates = sorted(pack_dir.glob(PDF_GLOB))
        pdf_path = pdf_candidates[0] if pdf_candidates else None
        if len(pdf_candidates) > 1:
            warnings.append(
                f"{len(pdf_candidates)} PDFs in pack; using '{pdf_path.name}'"
            )

        if ref_path is None:
            warnings.append("no 庫題卡碼文標碼 reference solution found in the pack")
        if sample_path is None:
            warnings.append("no 庫題卡測資範例 sample file found in the pack")
        if teda_path is None:
            warnings.append("no 庫題卡測資０隨輸入 TeDa generator found; no random hidden cases")

        code, title = _pack_identity(pack_dir, ref_path)

        ref_text = _read_text(ref_path)
        teda_text = _read_text(teda_path)
        rules_text = _read_text(rules_path)
        comparator_text = _read_text(comparator_path)
        readme_text = _read_text(readme_path)

        # ---- static sample pairs ------------------------------------------- #
        sample_cases: List[Dict[str, Any]] = []
        if sample_path is not None:
            raw_pairs = parse_sample_text(
                _read_text(sample_path) or ""
            )
            if not raw_pairs:
                warnings.append("sample file contained no 【範例輸入】/【範例輸出】 pairs")
            for stdin_text, expected_text in raw_pairs:
                sample_cases.append(
                    {
                        "input": _ensure_trailing_newline(stdin_text),
                        "expected_output": _ensure_trailing_newline(expected_text),
                        "is_sample": True,
                    }
                )

        # ---- dynamic hidden cases: TeDa generator x reference dual run ---- #
        hidden_cases: List[Dict[str, Any]] = []
        effective_time_limit = float(
            time_limit_sec
            or _time_limit_from_readme(readme_text)
            or 3.0
        )
        if generate_hidden and teda_text and ref_text and ref_text.strip():
            try:
                random_inputs = [
                    _ensure_trailing_newline(text)
                    for text in teda_engine.generate_random_inputs(
                        teda_text, num_random=num_random, seed=seed
                    )
                ]
                reference_outputs = teda_engine.run_dual_run(
                    ref_text,
                    ref_language,
                    random_inputs,
                    time_limit_sec=effective_time_limit,
                )
                for stdin_text, expected_text in zip(random_inputs, reference_outputs):
                    hidden_cases.append(
                        {
                            "input": stdin_text,
                            "expected_output": expected_text,
                            "is_sample": False,
                        }
                    )
                logger.info(
                    "problem pack %s: generated %d hidden cases via TeDa + %s reference",
                    code or title,
                    len(hidden_cases),
                    ref_language,
                )
            except (teda_engine.TeDaError, teda_engine.DualRunError, ValueError) as exc:
                # A broken generator/reference must not lose the static pack.
                warnings.append(f"hidden test case generation failed: {exc}")
                hidden_cases = []
        elif generate_hidden and (teda_text and not (ref_text and ref_text.strip())):
            warnings.append(
                "TeDa generator present but reference solution missing/empty; "
                "hidden cases skipped"
            )

        # ---- persist the PDF when the pack came from a zip ----------------- #
        persisted_pdf: Optional[str] = None
        if pdf_path is not None:
            if source["kind"] == "dir":
                persisted_pdf = str(pdf_path.resolve())
            elif pdf_dir is not None:
                target_dir = Path(pdf_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                prefix = f"{code}_" if code else ""
                target = target_dir / f"{prefix}{pdf_path.name}"
                shutil.copy2(pdf_path, target)
                persisted_pdf = str(target.resolve())
            else:
                warnings.append(
                    f"PDF '{pdf_path.name}' only exists inside the zip; "
                    "pass pdf_dir=... to persist it"
                )

        # ---- description & metadata ------------------------------------------ #
        if readme_text and readme_text.strip():
            description = readme_text.strip()
        else:
            description = _generated_description(
                code, title, len(sample_cases), len(hidden_cases)
            )

        return {
            "problem_code": code,
            "title": title,
            "description": description,
            "difficulty": _difficulty_from_readme(readme_text),
            "time_limit_sec": effective_time_limit,
            "memory_limit_mb": _memory_limit_from_readme(readme_text) or 256,
            # Students start from a blank editor; the reference is never exposed.
            "starter_code_c": "",
            "starter_code_cpp": "",
            "ref_code": ref_text,
            "ref_language": ref_language if ref_text else None,
            "teda_random_script": teda_text,
            "wording_rules": rules_text,
            "output_comparator": comparator_text,
            "pdf_path": persisted_pdf,
            "sample_test_cases": sample_cases,
            "hidden_test_cases": hidden_cases,
            "test_cases": sample_cases + hidden_cases,
            "pack_name": pack_dir.name,
            "source": source,
            "warnings": warnings,
        }
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
