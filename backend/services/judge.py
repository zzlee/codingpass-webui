"""Judge and rubric grading service for CodingPass."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

try:
    from ..compiler import RunResult, Status, run_code_batch
    from ..config import (
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        RUBRIC_SAMPLE_POINTS,
        VERDICT_DETAIL_CHARS,
    )
    from ..models import SubmissionStatus, TestCase
    from ..patch_probe import PATCHED_PASS_SCORE
    from ..schemas import CaseVerdict, PatchProbeReport
    from ..teda_engine import compare_outputs
except ImportError:
    from compiler import RunResult, Status, run_code_batch  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        RUBRIC_COMPILE_POINTS,
        RUBRIC_LOGIC_POINTS,
        RUBRIC_QUALITY_POINTS,
        RUBRIC_RANDOM_POINTS,
        RUBRIC_SAMPLE_POINTS,
        VERDICT_DETAIL_CHARS,
    )
    from models import SubmissionStatus, TestCase  # type: ignore[no-redef]
    from patch_probe import PATCHED_PASS_SCORE  # type: ignore[no-redef]
    from schemas import CaseVerdict, PatchProbeReport  # type: ignore[no-redef]
    from teda_engine import compare_outputs  # type: ignore[no-redef]


def _normalize_output(text: Optional[str]) -> str:
    r"""Canonical form used to compare program output with the expected answer.

    Ignored: carriage returns, trailing spaces/tabs on each line, and leading or
    trailing blank lines. Still significant: line order, internal spacing,
    spelling and capitalisation.
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
        """True when any passing case only passed thanks to trimmed output."""
        return any(
            outcome.passed and outcome.presentation_warning for outcome in self.outcomes
        )

    def aggregate_status(self, total_count: int) -> str:
        """Collapse the per-case verdicts into the stored submission verdict."""
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
    """Compile code once, run it on every test case and score the outputs."""
    report = JudgeReport()
    results: list[RunResult] = run_code_batch(
        code,
        language,
        [case_row.input for case_row in cases],
        timeout_seconds,
        memory_limit_mb=memory_limit_mb,
    )
    if len(results) != len(cases):
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
            report.compile_output = result.compile_output
        report.compile_time_ms = max(report.compile_time_ms, result.compile_time_ms)

        expected = case_row.expected_output or ""
        comparison = compare_outputs(result.stdout, expected)
        passed = result.status == Status.SUCCESS and _normalize_output(
            result.stdout
        ) == _normalize_output(expected)
        presentation_warning = bool(passed and not comparison["strict_match"])
        if result.status == Status.SUCCESS:
            case_status = SubmissionStatus.ACCEPTED if passed else SubmissionStatus.WRONG_ANSWER
        else:
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
    """Render one outcome, redacting hidden data for callers not entitled to it."""
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
        presentation_warning=bool(outcome.presentation_warning and visible),
        input=outcome.stdin if visible else None,
        expected_output=outcome.expected_output if visible else None,
        stdout=outcome.stdout if visible else None,
        stderr=(_clip(outcome.stderr) if visible else "") or None,
    )


def _probe_cases(cases: list[TestCase], limit: int = 3) -> list[dict[str, str]]:
    """Pick the public cases the Minimal Patch Probe is allowed to run."""
    picked = [case for case in cases if case.is_sample][:limit]
    if not picked and cases:
        picked = cases[:1]
    return [
        {"input": case.input or "", "expected_output": case.expected_output or ""}
        for case in picked
    ]


def _probe_public(probe: Optional[dict]) -> Optional[PatchProbeReport]:
    """Project a probe result onto the wire format."""
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
    """The 100-point teaching rubric for one graded submission."""

    compile_score: float = 0.0
    sample_score: float = 0.0
    random_score: float = 0.0
    logic_score: float = 0.0
    quality_score: float = 0.0

    @property
    def total(self) -> float:
        return round(
            min(
                100.0,
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
    """Turn one JudgeReport into the five rubric parts."""
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
            logic_score = round(min(logic_score, PATCHED_PASS_SCORE), 2)
    if presentation_warning:
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
    """``passed / total * band``, ``0.0`` when total <= 0."""
    if total <= 0:
        return 0.0
    return round(band * passed / total, 2)


def passed_hidden(hidden: list[CaseOutcome]) -> int:
    return sum(1 for outcome in hidden if outcome.passed)


def _wording_json(violations: Optional[list[str]]) -> Optional[str]:
    """Serialise the human-readable violation list for ``wording_violations``."""
    if not violations:
        return None
    try:
        return json.dumps(list(violations), ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps([str(item) for item in violations])


def _wording_list(stored: Optional[str]) -> list[str]:
    """Decode ``Submission.wording_violations`` back into a display list."""
    if not stored:
        return []
    try:
        decoded = json.loads(stored)
    except ValueError:
        return [stored]
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return [str(decoded)]
