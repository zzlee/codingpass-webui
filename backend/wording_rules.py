"""Static wording / structure rules for CodingPass submissions.

Ported from ``codingpass-tests/tools/cp_sandbox.py`` (``check_wording_rules``)
and adapted for the web backend: the rule text is passed around as a plain
string (it lives in ``Problem.wording_rules``) instead of a file path, and the
result carries a human-readable ``violations`` list for the API layer.

Rule file grammar (one directive per line, blanks/unmatched lines ignored)
--------------------------------------------------------------------------
``#term[<count>;<flags>] <literal>``
    Count whole-word occurrences of ``<literal>`` (word boundaries are applied
    when the pattern is a bare identifier, otherwise a plain substring count).
``#root[<count>;<flags>] <literal>``
    Count substring occurrences of ``<literal>`` anywhere in the source.
``#regex[<count>;<flags>] <pattern>``
    Count matches of the Python regular expression ``<pattern>``.

``<count>`` is either an exact number (``0``, ``3``) or an inclusive range
(``1~3``); the upper bound may be ``*`` for "no limit" (``1~*``), and a lone
``*`` means "anything goes".

``<flags>`` is a whitespace / comma separated set of:

``skipComments``
    C/C++ ``//``, ``/* */`` comments are blanked out before matching.
``skipStrings``
    String and character literals are replaced with ``""`` before matching.
``caseInsensitive``
    Match case-insensitively (applies to all three directives).

Example (``*_庫題卡碼文限碼.txt``)::

    #term[0;skipComments skipStrings] system
    #term[0] <ctime>
    #regex[1~*;skipComments skipStrings] \b(for|while)\b

Typical use from the judge::

    verdict = check_wording_rules(source_code, problem.wording_rules or "")
    if not verdict["wording_pass"]:
        ...  # reject / penalise, show verdict["violations"] to the student
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["check_wording_rules", "strip_all_comments_and_strings"]

#: ``#term[0;skipComments] system`` -> (directive, count spec, flags, pattern).
_RULE_LINE = re.compile(
    r"^#(term|root|regex)\[([^;\]]+)(?:;([^\]]*))?\]\s+(.+)$"
)

#: One regex pass that recognises every C/C++ comment and string/char literal.
_COMMENT_AND_STRING = re.compile(
    r'(//[^\n]*|/\*.*?\*/)|("(\\.|[^"\\])*"|\'(\\.|[^\'\\])*\')',
    re.DOTALL,
)


def strip_all_comments_and_strings(
    code: str,
    strip_comments: bool = True,
    strip_strings: bool = True,
) -> str:
    """Blank out C/C++ comments and/or string literals in a single regex pass.

    Comments become a single space, string/char literals become ``""`` so that
    token adjacency (and line structure) stays roughly intact for the rule
    matcher. Quoted-literal bodies are matched with escape awareness, so a
    stray quote inside ``"say \"hi\""`` cannot open a fake comment region.
    """
    pattern = _COMMENT_AND_STRING

    def replacer(match: "re.Match[str]") -> str:
        comment, literal = match.group(1), match.group(2)
        if comment is not None:
            return " " if strip_comments else comment
        if literal is not None:
            return '""' if strip_strings else literal
        return match.group(0)

    return pattern.sub(replacer, code or "")


def _parse_count_spec(spec: str) -> Tuple[int, float]:
    """Parse ``"0"`` / ``"1~3"`` / ``"1~*"`` / ``"*"`` into ``(lo, hi)``.

    Raises :class:`ValueError` for malformed specs; the caller records it as a
    rule error instead of crashing the whole evaluation.
    """
    spec = spec.strip()
    if spec == "*":
        return 0, float("inf")
    if "~" in spec:
        lo_s, hi_s = spec.split("~", 1)
        lo = int(lo_s.strip())
        hi = float("inf") if hi_s.strip() in ("", "*") else int(hi_s.strip())
        if hi < lo:
            raise ValueError(f"empty count range {spec!r}")
        return lo, hi
    exact = int(spec)
    return exact, exact


def _parse_flags(flags_str: str) -> Tuple[bool, bool, int]:
    """Return ``(skip_comments, skip_strings, compiled re flags)``."""
    lowered = (flags_str or "").lower()
    skip_comments = "skipcomments" in lowered
    skip_strings = "skipstrings" in lowered
    re_flags = re.IGNORECASE if "caseinsensitive" in lowered else 0
    return skip_comments, skip_strings, re_flags


def _count_matches(
    directive: str, pattern: str, target_code: str, re_flags: int
) -> int:
    """Apply one directive to the (already stripped) source text."""
    if directive == "term":
        # Bare identifiers get word boundaries; symbols like ``<ctime>`` do not.
        if re.fullmatch(r"\w+", pattern):
            regex = rf"\b{re.escape(pattern)}\b"
        else:
            regex = re.escape(pattern)
        return len(re.findall(regex, target_code, re_flags))
    if directive == "root":
        return len(re.findall(re.escape(pattern), target_code, re_flags))
    # directive == "regex": the pattern itself may carry inline flags too.
    return len(re.findall(pattern, target_code, re_flags))


def check_wording_rules(
    code: str, rules_text: Optional[str] = None
) -> Dict[str, Any]:
    """Evaluate CodingPass TeDa-Wording rules (``#term`` / ``#root`` / ``#regex``).

    Args:
        code: the submission source text.
        rules_text: the rule file content (``Problem.wording_rules``). Empty,
            ``None`` or an all-comment text passes trivially.

    Returns:
        ``{"wording_pass": bool, "rules": [...], "violations": [...]}`` where
        each rule entry reports ``rule`` (the original line), ``directive``,
        ``pattern``, ``expected_range``, ``actual_count``, ``passed`` and an
        optional ``error`` note; ``violations`` is a ready-to-display list of
        human-readable strings for the failed (or malformed) rules.
    """
    rules_out: List[Dict[str, Any]] = []
    violations: List[str] = []
    all_passed = True

    if not rules_text or not rules_text.strip():
        return {"wording_pass": True, "rules": rules_out, "violations": violations}

    source = code or ""
    # Cache stripped variants: several rules usually share one flag combination.
    strip_cache: Dict[Tuple[bool, bool], str] = {}

    for raw_line in rules_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        match = _RULE_LINE.match(line)
        if match is None:
            # Not a wording directive (e.g. a plain ``#include`` note): ignore.
            continue

        directive, count_spec, flags_str, pattern = match.groups()
        pattern = pattern.strip()
        skip_comments, skip_strings, re_flags = _parse_flags(flags_str or "")

        cache_key = (skip_comments, skip_strings)
        target_code = strip_cache.get(cache_key)
        if target_code is None:
            target_code = strip_all_comments_and_strings(
                source, strip_comments=skip_comments, strip_strings=skip_strings
            )
            strip_cache[cache_key] = target_code

        entry: Dict[str, Any] = {
            "rule": line,
            "directive": directive,
            "pattern": pattern,
            "expected_range": count_spec.strip(),
            "actual_count": 0,
            "passed": False,
        }

        try:
            lo, hi = _parse_count_spec(count_spec)
        except ValueError as exc:
            entry["error"] = f"malformed count spec: {exc}"
            violations.append(f"{line}  ->  invalid count spec ({exc})")
            all_passed = False
            rules_out.append(entry)
            continue

        try:
            count = _count_matches(directive, pattern, target_code, re_flags)
        except re.error as exc:
            entry["error"] = f"invalid regular expression: {exc}"
            violations.append(f"{line}  ->  invalid regex ({exc})")
            all_passed = False
            rules_out.append(entry)
            continue

        entry["actual_count"] = count
        entry["passed"] = lo <= count <= hi
        if not entry["passed"]:
            all_passed = False
            expected = entry["expected_range"]
            violations.append(
                f"{line}  ->  matched {count} time(s), expected {expected}"
            )
        rules_out.append(entry)

    return {"wording_pass": all_passed, "rules": rules_out, "violations": violations}
