"""Minimal Patch Probe: how little surgery makes a submission work? (Stage 4)

The offline grading rubric in ``codingpass-tests/評分系統計劃書_v1.md`` (§6.6)
refuses to grade an algorithm purely by its test results: a student who wrote a
correct algorithm but typed one missing semicolon must not score like a student
who wrote nonsense. The offline workflow solved that by hand - the grader copied
``blind_src/<SID>.cpp`` to ``/tmp``, applied the obvious one-line fix, recompiled
and re-ran the sample inputs. This module automates exactly that loop inside the
hardened :mod:`compiler` sandbox so the web judge can report the same signal:

``probed_logic_score``
    ``40.0`` - already passes every sample case (nothing to probe).
    ``32.0`` - one or two mechanical fixes (a ``;``, a header, a loop bound) made
    the samples pass: the algorithm is sound.
    ``10.0`` - no candidate reached a full pass: the algorithm itself needs work.

Usage from the judge::

    probe = probe_minimal_patch(
        submission.code, submission.language,
        [{"input": ..., "expected_output": ...}], 3.0,
    )
    if probe["patch_success"]:      # "Fixed minor syntax/boundary issue; ..."
        ...

Safety
------
Every candidate - original **and** patched - is compiled and executed by
:func:`compiler.run_code`, i.e. in a private temporary directory under
``RLIMIT_CPU`` / ``RLIMIT_AS`` / ``RLIMIT_FSIZE`` / ``RLIMIT_CORE=0``, in its own
process group (``os.setsid`` + ``killpg`` on timeout), with a sanitised
environment and capped output. The probe never executes anything
``POST /api/run`` would not, never relaxes a limit, and never writes outside the
sandbox. Its cost is bounded by :data:`MAX_PROBE_RUNS` and
:data:`MAX_PROBE_SECONDS`; when either runs out the probe reports what it found
instead of holding a judge slot forever.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

try:  # package-style launch: `uvicorn backend.main:app`
    from . import compiler as _compiler
    from .compiler import RunResult, Status
    from .teda_engine import compare_outputs
    from .wording_rules import strip_all_comments_and_strings
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    import compiler as _compiler  # type: ignore[no-redef]
    from compiler import RunResult, Status  # type: ignore[no-redef]
    from teda_engine import compare_outputs  # type: ignore[no-redef]
    from wording_rules import strip_all_comments_and_strings  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.patch_probe")

__all__ = [
    "probe_minimal_patch",
    "PatchProbe",
    "CLEAN_PASS_SCORE",
    "PATCHED_PASS_SCORE",
    "BROKEN_ALGORITHM_SCORE",
]

# --------------------------------------------------------------------------- #
# Rubric constants
# --------------------------------------------------------------------------- #

#: Everything runs: the submission passed every sample case as it stands.
CLEAN_PASS_SCORE: float = 40.0
#: A mechanical fix (semicolon, header, loop bound) made the samples pass.
PATCHED_PASS_SCORE: float = 32.0
#: The probe could not reach a working program: the algorithm needs a rewrite.
BROKEN_ALGORITHM_SCORE: float = 10.0


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default
    return value if value > 0 else default


#: Sample cases the probe executes per candidate.
MAX_PROBE_CASES: int = _env_int("PROBE_MAX_CASES", 4)
#: Hard cap on sandbox runs burned by one probe (each candidate costs >= 1).
MAX_PROBE_RUNS: int = _env_int("PROBE_MAX_RUNS", 20)
#: Wall-clock budget for one probe.
MAX_PROBE_SECONDS: float = _env_float("PROBE_MAX_SECONDS", 25.0)
#: How many diagnostic-driven repair rounds one candidate may receive.
MAX_REPAIR_ROUNDS: int = _env_int("PROBE_MAX_ROUNDS", 4)
#: Per-case timeout ceiling: a probe never outlives the judge's own bounds.
MAX_PROBE_TIMEOUT_SEC: float = _env_float("PROBE_MAX_TIMEOUT_SEC", 20.0)

# --------------------------------------------------------------------------- #
# Fixer plumbing
# --------------------------------------------------------------------------- #

#: A ``gcc``/``g++`` diagnostic location, e.g.
#: ``/tmp/cj-cpp-abc/solution.cpp:12:5: error: expected ';' before 'return'``.
_DIAGNOSTIC_RE = re.compile(
    r":(?P<line>\d+):(?P<col>\d+):\s*(?:fatal\s+)?error:\s*(?P<msg>.*)"
)

#: Identifier -> declaring header, used for ``'stringstream' was not declared``.
_HEADER_FOR_NAME: Dict[str, str] = {
    # C++ standard library
    "stringstream": "sstream",
    "istringstream": "sstream",
    "ostringstream": "sstream",
    "string": "string",
    "getline": "string",
    "vector": "vector",
    "deque": "deque",
    "list": "list",
    "stack": "stack",
    "queue": "queue",
    "priority_queue": "queue",
    "map": "map",
    "set": "set",
    "unordered_map": "unordered_map",
    "unordered_set": "unordered_set",
    "pair": "utility",
    "sort": "algorithm",
    "reverse": "algorithm",
    "swap": "algorithm",
    "max_element": "algorithm",
    "min_element": "algorithm",
    "upper_bound": "algorithm",
    "lower_bound": "algorithm",
    "accumulate": "numeric",
    "iota": "numeric",
    "sqrt": "cmath",
    "pow": "cmath",
    "fabs": "cmath",
    "ceil": "cmath",
    "floor": "cmath",
    "log": "cmath",
    "memset": "cstring",
    "memcpy": "cstring",
    "strlen": "cstring",
    "strcpy": "cstring",
    "strcmp": "cstring",
    "size_t": "cstddef",
    "cout": "iostream",
    "cin": "iostream",
    "cerr": "iostream",
    "endl": "iostream",
    # C standard library
    "printf": "stdio.h",
    "scanf": "stdio.h",
    "sprintf": "stdio.h",
    "fprintf": "stdio.h",
    "fgets": "stdio.h",
    "malloc": "stdlib.h",
    "calloc": "stdlib.h",
    "free": "stdlib.h",
    "atoi": "stdlib.h",
    "exit": "stdlib.h",
    "bool": "stdbool.h",
}

#: Names that need ``using namespace std;`` when they appear unqualified.
_STD_NAMES = frozenset(
    {
        "cout", "cin", "cerr", "endl", "string", "vector", "stack", "queue",
        "deque", "list", "map", "set", "stringstream", "istringstream",
        "ostringstream", "pair", "sort", "reverse", "swap", "getline", "max",
        "min", "size_t",
    }
)

#: ``#include <x>`` / ``#include "x"``.
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)

#: A ``for`` header: ``for (init; condition; step)``.
_FOR_HEADER_RE = re.compile(r"\bfor\s*\(([^;{}]*);([^;{}]*);([^;{}]*)\)")

#: The comparison inside a ``for`` header's condition clause.
_BOUND_RE = re.compile(
    r"(?P<lhs>[A-Za-z_]\w*|\d+)\s*(?P<op><=|<)\s*(?P<rhs>[A-Za-z_]\w*|\d+)"
)

#: ``'X' was not declared in this scope`` - the name simply was never declared.
_UNDECLARED_RE = re.compile(
    r"'([A-Za-z_]\w*)'\s*(?:was not declared in this scope|was not declared"
    r"|has not been declared|undeclared|does not name a type)"
)

#: ``variable 'std::stringstream ss' has initializer but incomplete type``:
#: the *class* is missing, which in student code always means one missing header.
_INCOMPLETE_TYPE_RE = re.compile(
    r"(?:variable|parameter|declaration)\s*'[^']*?\b(?:std::)?([A-Za-z_]\w*)"
    r"[^']*'\s*(?:has initializer but incomplete type|has incomplete type"
    r"|declared as incomplete type)"
    r"|invalid use of incomplete type\s*'(?:std::)?([A-Za-z_]\w*)'"
)

#: Diagnostics that all mean "the statement above never ended". GCC words them
#: differently depending on what followed, but the repair is always one ``;``.
#: ``initializer``/``unqualified-id`` come back unquoted, ``;`` comes back quoted.
#: Diagnostics that all mean "the statement above never ended". GCC words them
#: differently depending on what followed (``expected ';' before 'x'``,
#: ``expected initializer before 'x'``, ``expected unqualified-id before 'x'``),
#: but the repair is always the same single character.
_MISSING_SEMICOLON_RE = re.compile(
    r"expected\s*'?(?:;|initializer|unqualified-id)'?\s*before"
)

#: ``cin.getline(line)`` where ``line`` is a ``std::string``: the student mixed
#: up the stream member (char buffer) with the free function ``getline(cin, s)``.
_CIN_GETLINE_RE = re.compile(r"\b(cin|std::cin)\.getline\s*\(\s*([A-Za-z_]\w*)\s*\)")


@dataclass(frozen=True)
class _Fix:
    """A named, deterministic source transformation.

    ``apply(source, language, compile_output)`` returns the patched source, or
    ``None`` when the fixer has nothing to contribute.
    """

    name: str
    apply: Callable[[str, str, str], Optional[str]]


@dataclass
class PatchProbe:
    """Structured result of :func:`probe_minimal_patch`."""

    needed: bool
    patch_success: bool
    probed_logic_score: float
    details: str
    #: Judge verdict of the submission exactly as the student wrote it.
    baseline_status: str = ""
    #: Fixers that produced the working variant (empty unless one worked).
    applied_fixes: List[str] = field(default_factory=list)
    #: Every fixer the probe tried, in order (diagnostics for the teacher).
    attempted_fixes: List[str] = field(default_factory=list)
    #: Sample cases executed for the best candidate.
    cases_tested: int = 0
    #: Sample cases the best candidate passed.
    cases_passed: int = 0
    #: Compile diagnostics of the submission (clipped).
    compile_output: str = ""
    #: Patched source; only present when a patch actually worked.
    patched_code: Optional[str] = None
    #: The sandbox budget stopped the probe early.
    stopped_early: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needed": self.needed,
            "patch_success": self.patch_success,
            "probed_logic_score": self.probed_logic_score,
            "details": self.details,
            "baseline_status": self.baseline_status,
            "applied_fixes": list(self.applied_fixes),
            "attempted_fixes": list(self.attempted_fixes),
            "cases_tested": self.cases_tested,
            "cases_passed": self.cases_passed,
            "compile_output": self.compile_output,
            "patched_code": self.patched_code,
            "stopped_early": self.stopped_early,
        }


# --------------------------------------------------------------------------- #
# Fixer: missing semicolons
# --------------------------------------------------------------------------- #


def _lines(code: str) -> List[str]:
    return code.replace("\r\n", "\n").replace("\r", "\n").split("\n")


#: GCC quotes identifiers with U+2018/U+2019, which would force four quote
#: spellings into every pattern below; folding them once is cheaper.
_QUOTE_FOLD = {"\u2018": "'", "\u2019": "'", "\u201c": "'", "\u201d": "'",
               "\u300c": "'", "\u300d": "'"}


def _plain_quotes(compile_output: Optional[str]) -> str:
    """Return ``compile_output`` with typographic quotes folded to ASCII."""
    if not compile_output:
        return ""
    return "".join(_QUOTE_FOLD.get(char, char) for char in compile_output)


#: Lines that legitimately have no terminator.
_IGNORE_FOR_SEMICOLON_RE = re.compile(
    r"^(if|for|while|switch|else|do|case|default|public|private|protected"
    r"|class|struct|union|enum|namespace|template|try|catch)\b"
)


def _already_terminated(stripped: str) -> bool:
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return True
    return stripped[-1] in ";{}:," or stripped.endswith("*/")


def _statement_like(stripped: str) -> bool:
    """Does this line read like a statement whose terminator is missing?"""
    if _IGNORE_FOR_SEMICOLON_RE.match(stripped):
        return False
    # assignment / compound assignment, call, declaration, or return.
    return bool(
        re.match(r"^[A-Za-z_][\w:<>,\s\*&\[\]]*[\w>\]]\s*=\s*\S", stripped)
        or re.match(r"^[A-Za-z_][\w:<>,\s\*&]*[\w>\]]\s*(\+\+|--|\+=|-=|\*=|/=|%=)\s*\S", stripped)
        or re.match(r"^[\w:]+(\.\w+)*\s*\(.*\)$", stripped)
        or re.match(r"^(?:[\w:]+(<[^>]*>)?\s+)+[\w*&]+\s*(\[[^\]]*\])?$", stripped)
        or re.match(r"^return\b.*$", stripped)
    )


def _fix_missing_semicolons(code: str, language: str, compile_output: str) -> Optional[str]:
    """Insert the ``;`` that ``expected ';' before 'x'`` is asking for.

    The diagnostic points at the token *after* the slip, so the terminator
    belongs at the end of the previous statement line. When no usable
    diagnostic is available a conservative textual heuristic is used instead.
    """
    lines = _lines(code)
    touched = 0

    for match in _DIAGNOSTIC_RE.finditer(_plain_quotes(compile_output)):
        if not _MISSING_SEMICOLON_RE.search(match.group("msg")):
            continue
        index = int(match.group("line")) - 2  # 0-based line above the reported one
        while index >= 0 and not lines[index].strip():
            index -= 1
        if index < 0:
            continue
        stripped = lines[index].strip()
        if _already_terminated(stripped) or stripped.startswith("#"):
            continue
        lines[index] = _terminate_before_comment(lines[index])
        touched += 1

    if not touched:
        depth = 0
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            depth += line.count("{") - line.count("}")
            if depth <= 0 or _already_terminated(stripped):
                continue
            if _statement_like(stripped):
                lines[index] = line.rstrip() + ";"
                touched += 1
                if touched >= 6:  # heuristic path: stay minimal
                    break

    return "\n".join(lines) if touched else None


# --------------------------------------------------------------------------- #
# Fixers: missing includes / using-directive
# --------------------------------------------------------------------------- #


def _insert_after_preamble(code: str, snippet: str) -> str:
    """Insert ``snippet`` right after the last ``#include`` (or at the top)."""
    ends = [match.end() for match in _INCLUDE_RE.finditer(code)]
    if not ends:
        return snippet + "\n" + code
    newline = code.find("\n", max(ends))
    if newline == -1:
        return code.rstrip("\n") + "\n" + snippet + "\n"
    return code[: newline + 1] + snippet + "\n" + code[newline + 1 :]


def _included_names(code: str) -> set:
    return {name.strip().lower() for name in _INCLUDE_RE.findall(code)}


def _blamed_identifiers(compile_output: Optional[str]) -> List[str]:
    """Names the compiler cannot resolve, in diagnostic order.

    Covers both ``'X' was not declared in this scope`` (a missing name) and
    ``variable 'std::X x' has initializer but incomplete type`` (a missing
    class), because both are repaired by adding exactly one ``#include``.
    """
    names: List[str] = []
    for match in _DIAGNOSTIC_RE.finditer(_plain_quotes(compile_output)):
        message = match.group("msg")
        found = _UNDECLARED_RE.search(message)
        if found:
            if found.group(1) not in names:
                names.append(found.group(1))
            continue
        incomplete = _INCOMPLETE_TYPE_RE.search(message)
        if incomplete:
            for group in incomplete.groups():
                if group and group not in names:
                    names.append(group)
    return names


def _terminate_before_comment(line: str) -> str:
    """Append ``;`` to ``line``, keeping any trailing ``//`` comment readable."""
    marker = line.find("//")
    if marker == -1:
        return line.rstrip() + ";"
    return line[:marker].rstrip() + "; " + line[marker:].lstrip()


def _fix_missing_includes(code: str, language: str, compile_output: str) -> Optional[str]:
    """Add the header that declares the identifier the compiler cannot find."""
    if not compile_output:
        return None
    have = _included_names(code)
    missing: List[str] = []
    for name in _blamed_identifiers(compile_output):
        header = _HEADER_FOR_NAME.get(name)
        # <stdio.h> and <cstdio> declare the same names; do not double up.
        if not header or header in missing:
            continue
        if header in have or header.rstrip(">") in have:
            continue
        if header.endswith(".h") and header[:-3] in have:
            continue
        missing.append(header)
    if not missing:
        return None
    return _insert_after_preamble(code, "\n".join(f"#include <{name}>" for name in missing))


def _fix_missing_using_namespace(code: str, language: str, compile_output: str) -> Optional[str]:
    """Add ``using namespace std;`` when unqualified std names are blamed."""
    if language != "cpp" or not compile_output or "using namespace std" in code:
        return None
    blamed = set(_blamed_identifiers(compile_output))
    if not blamed or not (blamed & _STD_NAMES):
        return None
    return _insert_after_preamble(code, "using namespace std;")


# --------------------------------------------------------------------------- #
# Fixers: other structural slips
# --------------------------------------------------------------------------- #


def _fix_unbalanced_braces(code: str, language: str, compile_output: str) -> Optional[str]:
    """Close the blocks the student forgot (``expected '}' at end of input``)."""
    cleaned = strip_all_comments_and_strings(code, strip_comments=True, strip_strings=True)
    balance = cleaned.count("{") - cleaned.count("}")
    if balance <= 0 or balance > 4:
        return None
    return code.rstrip("\n") + "\n" + ("}\n" * balance)


def _fix_main_return_type(code: str, language: str, compile_output: str) -> Optional[str]:
    """``main() { ... }`` written without a return type -> ``int main()``."""
    if not compile_output or not re.search(
        r"undefined reference to ['\"]?main|definition of ['\"]?main",
        _plain_quotes(compile_output),
    ):
        return None
    patched = re.sub(
        r"(?m)^(?!\s*(?:int|void|long|short|unsigned|signed|auto|static|inline)\b)"
        r"(\s*)main\s*\(",
        r"\1int main(",
        code,
        count=1,
    )
    return patched if patched != code else None


def _fix_cin_getline(code: str, language: str, compile_output: str) -> Optional[str]:
    """``cin.getline(s)`` -> ``getline(cin, s)`` when the blame mentions getline."""
    if not compile_output or ".getline(" not in code:
        return None
    if "getline" not in _plain_quotes(compile_output):
        return None
    patched = _CIN_GETLINE_RE.sub(r"getline(\1, \2)", code)
    patched = patched.replace("getline(std::cin, ", "getline(cin, ")
    return patched if patched != code else None


def _fix_vector_push(code: str, language: str, compile_output: str) -> Optional[str]:
    """``my_vector.push(x)`` -> ``my_vector.push_back(x)`` (a student classic)."""
    if "vector" not in code or ".push(" not in code:
        return None
    if compile_output and "push" not in _plain_quotes(compile_output):
        return None
    patched = re.sub(r"\.push\(", ".push_back(", code)
    return patched if patched != code else None


#: Compile-error fixers, cheapest and most likely first.
_COMPILE_FIXERS: Tuple[_Fix, ...] = (
    _Fix("missing-semicolon", _fix_missing_semicolons),
    _Fix("missing-include", _fix_missing_includes),
    _Fix("missing-using-namespace-std", _fix_missing_using_namespace),
    _Fix("vector-push-back", _fix_vector_push),
    _Fix("cin-getline-to-getline", _fix_cin_getline),
    _Fix("main-return-type", _fix_main_return_type),
    _Fix("unclosed-brace", _fix_unbalanced_braces),
)


# --------------------------------------------------------------------------- #
# Fixers: off-by-one loop boundaries (wrong-answer candidates)
# --------------------------------------------------------------------------- #


def _boundary_variants(code: str) -> List[Tuple[str, str]]:
    """Variants of ``code`` with ``for`` loop bounds flipped (``<`` <-> ``<=``).

    Ordered so the *smallest* edit is tried first, which is the whole point of a
    minimal patch: one flipped bound first, all of them flipped last.
    """
    flips: List[Tuple[int, int, str, str]] = []  # (start, end, replacement, label)
    for position, header in enumerate(_FOR_HEADER_RE.finditer(code), start=1):
        condition = header.group(2)
        bound = _BOUND_RE.search(condition)
        if bound is None:
            continue
        flipped = {"<=": "<", "<": "<="}[bound.group("op")]
        replacement = f"{bound.group('lhs')} {flipped} {bound.group('rhs')}"
        new_condition = condition[: bound.start()] + replacement + condition[bound.end() :]
        start, end = header.span(2)
        label = f"loop-boundary#{position}: '{bound.group('op')}' -> '{flipped}'"
        flips.append((start, end, new_condition, label))

    variants: List[Tuple[str, str]] = []
    for start, end, replacement, label in flips:
        variant = code[:start] + replacement + code[end:]
        if variant != code:
            variants.append((label, variant))

    if len(flips) > 1:
        combined = code
        # Right-to-left so the recorded spans stay valid as we splice.
        for start, end, replacement, _label in sorted(flips, reverse=True):
            combined = combined[:start] + replacement + combined[end:]
        if combined != code:
            variants.append((f"loop-boundary*{len(flips)}: all bounds flipped", combined))

    return variants


# --------------------------------------------------------------------------- #
# Sandbox helpers
# --------------------------------------------------------------------------- #


class _Budget:
    """Wall-clock + run-count budget shared by one whole probe."""

    def __init__(self, max_runs: int, max_seconds: float) -> None:
        self.max_runs = max(1, max_runs)
        self.max_seconds = max(0.5, max_seconds)
        self.started = time.monotonic()
        self.used = 0

    def spend(self) -> None:
        self.used += 1

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_runs or (time.monotonic() - self.started) >= self.max_seconds


def _error_signatures(compile_output: Optional[str]) -> Set[str]:
    """Distinct error texts, line numbers dropped.

    Fixing one slip always shifts the remaining line numbers, and a fix that
    *unlocks* further analysis usually reports **more** errors while actually
    making progress. Comparing the set of distinct messages answers the only
    question that matters: did this fix retire an error it did not retire
    before?
    """
    return {
        match.group("msg").strip()
        for match in _DIAGNOSTIC_RE.finditer(_plain_quotes(compile_output))
    }


def _clip(text: Optional[str], limit: int = 2000) -> str:
    """Bound compiler diagnostics so they are safe to store and return."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... clipped {len(text) - limit} characters]"


def _passes_all(
    source: str,
    language: str,
    cases: Sequence[Dict[str, str]],
    timeout: float,
    budget: _Budget,
) -> Tuple[bool, int, int, str, str]:
    """Run ``source`` against the sample cases.

    Returns ``(all_passed, passed, tested, status, compile_output)``. Stops at
    the first compile failure - every later case would re-measure the same
    broken build - and respects ``budget``.
    """
    passed = 0
    tested = 0
    status = Status.SUCCESS
    compile_output = ""
    timed_out = False
    crashed = False

    for case in cases:
        if budget.exhausted:
            break
        budget.spend()
        result: RunResult = _compiler.run_code(
            source, language, case.get("input") or "", timeout
        )
        if result.compile_output:
            compile_output = result.compile_output
        if result.status == Status.COMPILE_ERROR:
            return False, passed, tested + 1, Status.COMPILE_ERROR, result.compile_output
        if result.status == Status.INTERNAL_ERROR:
            return False, passed, tested + 1, Status.INTERNAL_ERROR, compile_output
        tested += 1
        if result.status == Status.TIME_LIMIT_EXCEEDED:
            timed_out = True
        elif result.status == Status.RUNTIME_ERROR:
            crashed = True
        elif compare_outputs(result.stdout, case.get("expected_output") or "")["trimmed_match"]:
            passed += 1

    if timed_out:
        status = Status.TIME_LIMIT_EXCEEDED
    elif crashed:
        status = Status.RUNTIME_ERROR
    all_passed = bool(cases) and passed == len(cases) and not budget.exhausted
    return all_passed, passed, tested, status, compile_output


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def probe_minimal_patch(
    code: str,
    language: str,
    test_cases: List[dict],
    time_limit_sec: float = 3.0,
) -> dict:
    """Measure how close a broken submission is to a working program.

    Args:
        code: the submission source.
        language: ``"c"`` or ``"cpp"``.
        test_cases: sample cases, each ``{"input": ..., "expected_output": ...}``.
            Pass the **public** samples: the probe is a teaching signal, and
            probing hidden cases would only burn judge time.
        time_limit_sec: per-case wall-clock limit handed to the sandbox (the
            judge passes the problem's own limit).

    Returns:
        ``{"needed": bool, "patch_success": bool, "probed_logic_score": float,
        "details": str, ...}`` - the four keys the rubric needs plus the
        teacher-facing extras ``baseline_status``, ``applied_fixes``,
        ``attempted_fixes``, ``cases_tested``, ``cases_passed``,
        ``compile_output``, ``patched_code`` and ``stopped_early``.

        ``details`` is one of ``"Clean pass"``,
        ``"Fixed minor syntax/boundary issue; algorithm is sound"``,
        ``"Algorithm requires substantial overhaul"``, or (in the degenerate
        cases of no cases / a dead sandbox) a sentence explaining why nothing
        could be probed, always paired with a ``0.0`` score so the caller
        never mistakes it for evidence of a working algorithm.
    """
    cases = [case for case in (test_cases or []) if isinstance(case, dict)][:MAX_PROBE_CASES]
    timeout = max(0.1, min(float(time_limit_sec), MAX_PROBE_TIMEOUT_SEC))
    budget = _Budget(MAX_PROBE_RUNS, MAX_PROBE_SECONDS)
    source = code or ""

    # A language the sandbox does not know is not the student's fault: report it
    # as "nothing could be probed" rather than "the algorithm is broken".
    try:
        _compiler.get_language_spec(language)
    except ValueError as exc:
        return PatchProbe(
            needed=False,
            patch_success=False,
            probed_logic_score=0.0,
            details=str(exc),
        ).to_dict()

    if not cases:
        return PatchProbe(
            needed=False,
            patch_success=False,
            probed_logic_score=0.0,
            details="No sample cases available to probe",
        ).to_dict()

    # ---- 1. baseline: does it already work? ------------------------------- #
    ok, passed, tested, baseline_status, compile_output = _passes_all(
        source, language, cases, timeout, budget
    )
    if baseline_status == Status.INTERNAL_ERROR:
        return PatchProbe(
            needed=False,
            patch_success=False,
            probed_logic_score=0.0,
            details="Sandbox unavailable; the probe was skipped",
            baseline_status=baseline_status,
            compile_output=_clip(compile_output),
            cases_tested=tested,
            cases_passed=passed,
        ).to_dict()

    if ok:
        return PatchProbe(
            needed=False,
            patch_success=False,
            probed_logic_score=CLEAN_PASS_SCORE,
            details="Clean pass",
            baseline_status=Status.SUCCESS,
            cases_tested=tested,
            cases_passed=passed,
        ).to_dict()

    report = PatchProbe(
        needed=True,
        patch_success=False,
        probed_logic_score=BROKEN_ALGORITHM_SCORE,
        details="Algorithm requires substantial overhaul",
        baseline_status=baseline_status,
        compile_output=_clip(compile_output),
        attempted_fixes=[],
        cases_tested=tested,
        cases_passed=passed,
    )
    attempted: List[str] = report.attempted_fixes

    # ---- 2. compile errors: iterative diagnostic-driven repair ------------ #
    candidate = source
    candidate_fixes: List[str] = []
    if baseline_status == Status.COMPILE_ERROR:
        current_output = compile_output
        current_signatures = _error_signatures(current_output)
        while len(candidate_fixes) < MAX_REPAIR_ROUNDS:
            if budget.exhausted:
                report.stopped_early = True
                break
            repaired = False
            for fixer in _COMPILE_FIXERS:
                if fixer.name in candidate_fixes or budget.exhausted:
                    continue
                patched = fixer.apply(candidate, language, current_output)
                if not patched or patched == candidate:
                    continue
                attempted.append(fixer.name)
                budget.spend()
                check = _compiler.run_code(
                    patched, language, cases[0].get("input") or "", timeout
                )
                signatures = _error_signatures(check.compile_output)
                if check.status != Status.COMPILE_ERROR or (
                    current_signatures - signatures
                ):
                    # The build survived, or at least this fix retired an error
                    # it had not retired before: adopt it and re-read the
                    # diagnostics, because the next slip is usually hiding
                    # behind the one just fixed.
                    candidate, current_output = patched, check.compile_output
                    current_signatures = signatures
                    candidate_fixes.append(fixer.name)
                    repaired = True
                    break
            if not repaired:
                break

        if candidate_fixes and candidate != source:
            ok, cand_passed, cand_tested, cand_status, cand_output = _passes_all(
                candidate, language, cases, timeout, budget
            )
            report.cases_tested = max(report.cases_tested, cand_tested)
            report.cases_passed = max(report.cases_passed, cand_passed)
            if ok:
                return _finish_success(report, candidate, candidate_fixes, cand_tested, cand_passed)
            # Keep the repaired source as the base for the boundary probes: a
            # student who missed a semicolon *and* an off-by-one is common.
            base_source, base_fixes = candidate, list(candidate_fixes)
            report.compile_output = _clip(cand_output) or report.compile_output
            if cand_status != Status.SUCCESS:
                report.baseline_status = cand_status
        else:
            base_source, base_fixes = source, []
    else:
        base_source, base_fixes = source, []

    # ---- 3. wrong answers: minimal off-by-one boundary flips ------------- #
    for label, variant in _boundary_variants(base_source):
        if budget.exhausted:
            report.stopped_early = True
            break
        attempted.append(label)
        budget.spend()
        quick = _compiler.run_code(
            variant, language, cases[0].get("input") or "", timeout
        )
        if quick.status in (Status.COMPILE_ERROR, Status.INTERNAL_ERROR):
            continue
        first_ok = compare_outputs(
            quick.stdout, cases[0].get("expected_output") or ""
        )["trimmed_match"]
        if quick.status != Status.SUCCESS or not first_ok:
            # A *minimal* patch that cannot even fix the first sample is noise.
            continue
        ok, cand_passed, cand_tested, _status, _out = _passes_all(
            variant, language, cases, timeout, budget
        )
        report.cases_tested = max(report.cases_tested, cand_tested)
        report.cases_passed = max(report.cases_passed, cand_passed)
        if ok:
            return _finish_success(report, variant, base_fixes + [label], cand_tested, cand_passed)

    if not report.patch_success:
        report.needed = False
        report.probed_logic_score = 0.0

    logger.info(
        "patch probe: status=%s tried=%d fixes=%s -> logic %.1f",
        report.baseline_status,
        len(attempted),
        ",".join(report.applied_fixes) or "-",
        report.probed_logic_score,
    )
    return report.to_dict()


def _finish_success(
    report: PatchProbe, candidate: str, fixes: List[str], tested: int, passed: int
) -> dict:
    """Finalise a probe in which a candidate reached a full sample pass."""
    report.needed = True
    report.patch_success = True
    report.probed_logic_score = PATCHED_PASS_SCORE
    report.details = "Fixed minor syntax/boundary issue; algorithm is sound"
    report.applied_fixes = list(fixes)
    report.patched_code = candidate
    report.cases_tested = max(report.cases_tested, tested)
    report.cases_passed = passed
    logger.info(
        "patch probe succeeded with %d fix(es): %s", len(fixes), ", ".join(fixes)
    )
    return report.to_dict()
