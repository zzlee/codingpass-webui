"""Structural plagiarism screening for CodingPass submissions (Stage 3).

Ported from ``codingpass-tests/tools/cp_sandbox.py``
(``normalize_cpp_structure`` / ``strip_all_comments_and_strings`` /
``compute_similarity_pairs``) and reshaped for the FastAPI backend: the inputs
are in-memory ``{student_key: source_text}`` mappings pulled out of the
:class:`models.Submission` table instead of a ``student-tests/`` folder, and the
result is a plain JSON-serialisable list the API can return verbatim.

How the detector works
----------------------
For every student the source is reduced to a *structural* token stream:

1. comments and string/char literals are blanked out
   (:func:`strip_all_comments_and_strings`),
2. ``#include`` directives are dropped (every student writes the same four),
3. ``for (; cond; )`` is canonicalised into ``while (cond)`` so a loop
   re-spelling cannot hide a copy,
4. every identifier that is **not** a C++ keyword / standard-library name is
   renamed to a positional placeholder (``VAR_1``, ``VAR_2``, ...), so
   ``sum[i] = a[i] + 1`` and ``total[j] = b[j] + 1`` collapse to the same stream.

Two structural streams are then compared with

* ``difflib.SequenceMatcher`` (longest ordered match: catches whole-block copies),
* a 3-gram Jaccard index (catches reordered / partially edited copies),

blended into ``combined_similarity = 0.6 * sequence + 0.4 * n-gram``. In
parallel the *raw* similarity of the comment-stripped sources (real identifier
names preserved) is measured as ``token_similarity``: renaming variables is the
cheapest way to disguise a copy, so identical variable spelling across two
accounts is the strongest single signal in the whole analysis.

Reference Baseline Subtraction
------------------------------
The naive threshold produces the worst possible false positive in a classroom:
sixty students who all independently wrote the textbook ``stringstream`` +
``vector`` solution look like a copying ring to a plain similarity score. When
``ref_code`` (``Problem.ref_code``, the teacher's ``*_庫題卡碼文標碼.cpp``) is
supplied, each student's structural similarity to that reference is measured
first. If **both** members of a pair are themselves near-identical to the
reference (``>= 0.85``), the pair's structural resemblance is *expected* and is
explained by the shared model answer, so the pair is only escalated when their
raw sources are also nearly identical (``token_similarity >= 0.92``) - i.e. one
of them still kept the other's (or the reference's) exact naming. Pairs
suppressed that way are returned with ``flagged = False`` plus a human-readable
``exemption``, so the teacher sees the candidate and the reason it was cleared
instead of wondering why a 0.97 pair is missing from the report.

Cost
----
``n`` students produce ``n * (n - 1) / 2`` pairs (60 students -> 1770 pairs).
Everything expensive is skipped behind cheap set maths: a pair only reaches
:dmod:`SequenceMatcher` when its 3-gram Jaccard is high enough that the blend
could possibly reach ``threshold``. Token streams are also capped at
:data:`MAX_STRUCTURE_TOKENS` so a pathological 200 KB submission cannot turn the
endpoint into a CPU DoS.

Example::

    >>> pairs = compute_similarity_pairs({"alice": src_a, "bob": src_b}, ref_code=ref)
    >>> [(p["student_a"], p["student_b"], p["flagged"]) for p in pairs]
    [('alice', 'bob', True)]
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from typing import Any, Dict, List, Mapping, Sequence, Set

try:  # package-style launch: `uvicorn backend.main:app`
    from .wording_rules import strip_all_comments_and_strings
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from wording_rules import strip_all_comments_and_strings  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.plagiarism")

__all__ = [
    "CPP_RESERVED_AND_STL",
    "strip_all_comments_and_strings",
    "normalize_cpp_structure",
    "compute_similarity_pairs",
    "structural_similarity",
    "reference_similarity",
]

# --------------------------------------------------------------------------- #
# Vocabulary of names that survive the renaming pass
# --------------------------------------------------------------------------- #

#: Keywords plus the standard-library names the detector keeps verbatim.
#: Everything else becomes ``VAR_<n>``, which is what makes a rename-resistant
#: comparison possible. The set is deliberately generous: a name that is
#: *usually* shared by classmates (``cout``, ``size``) is more informative when
#: kept than when renamed away.
CPP_RESERVED_AND_STL: frozenset = frozenset(
    {
        # --- keywords (C and C++) ------------------------------------------ #
        "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
        "bool", "break", "case", "catch", "char", "char16_t", "char32_t",
        "class", "compl", "const", "const_cast", "constexpr", "continue",
        "decltype", "default", "delete", "do", "double", "dynamic_cast", "else",
        "enum", "explicit", "export", "extern", "false", "float", "for",
        "friend", "goto", "if", "inline", "int", "long", "mutable", "namespace",
        "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or",
        "or_eq", "private", "protected", "public", "register",
        "reinterpret_cast", "return", "short", "signed", "sizeof", "static",
        "static_assert", "static_cast", "struct", "switch", "template", "this",
        "thread_local", "throw", "true", "try", "typedef", "typeid", "typename",
        "union", "unsigned", "using", "virtual", "void", "volatile", "wchar_t",
        "while", "xor", "xor_eq",
        # --- streams, containers, common algorithms ------------------------ #
        "std", "string", "vector", "stack", "queue", "deque", "list", "array",
        "set", "multiset", "map", "multimap", "unordered_map", "unordered_set",
        "pair", "tuple", "stringstream", "istringstream", "ostringstream",
        "cin", "cout", "cerr", "clog", "endl", "getline", "istream", "ostream",
        "ios", "ios_base", "sync_with_stdio", "tie", "swap", "size", "length",
        "NULL", "EOF", "push", "push_back", "push_front", "pop", "pop_back",
        "pop_front", "top", "front", "back", "empty", "clear", "resize",
        "begin", "end", "rbegin", "rend", "erase", "insert", "emplace",
        "substr", "find", "rfind", "c_str", "data", "at", "count", "reverse",
        "sort", "stable_sort", "max_element", "min_element", "accumulate",
        "min", "max", "abs", "fabs", "sqrt", "pow", "floor", "ceil", "round",
        "printf", "scanf", "puts", "gets", "fgets", "fscanf", "fprintf",
        "malloc", "calloc", "realloc", "free", "strlen", "strcpy", "strncpy",
        "strcmp", "strcat", "strstr", "memset", "memcpy", "atoi",
        "atof", "exit", "main",
    }
)

#: One C/C++ token: an identifier, a numeric literal, a multi-character
#: operator, or a single punctuation character.
_TOKEN_RE = re.compile(
    r"[A-Za-z_]\w*|\d+|==|!=|<=|>=|<<|>>|&&|\|\||\+\+|--"
    r"|[{}()\[\];,<>=+\-*/%&|^!~?:.]"
)

#: Identifiers are matched again on the *inside* of the token stream to decide
#: whether a token is a name (rename it) or a literal/operator (keep it).
_NAME_RE = re.compile(r"[A-Za-z_]\w*")

#: ``#include <...>`` / ``#include "..."`` lines, including the ``#  include``
#: spelling variants the preprocessor accepts.
_INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[^\n]*", re.MULTILINE)

#: ``for (; cond; )`` has exactly the meaning of ``while (cond)``.
_FOR_AS_WHILE_RE = re.compile(r"\bfor\s*\(\s*;\s*([^;]+?)\s*;\s*\)")

#: Type keywords that start a declaration segment. Used only to work out which
#: identifiers the submission declared itself (see :func:`_student_owned_names`).
_DECLARATION_TYPE_RE = re.compile(
    r"\b(?:int|int8_t|int16_t|int32_t|int64_t|uint32_t|uint64_t|long|short|char"
    r"|float|double|bool|string|auto|unsigned|signed|size_t|vector|array|deque"
    r"|stack|queue|priority_queue|list|set|multiset|map|multimap|unordered_set"
    r"|unordered_map|pair|tuple|bitset|stringstream|istringstream|ostringstream"
    r"|ifstream|ofstream|FILE)\b"
)

#: ``cin >> value`` / ``scanf("%d", &count)`` - both bind a name the student chose.
_EXTRACTION_TARGET_RE = re.compile(r">>\s*([A-Za-z_]\w*)|&\s*([A-Za-z_]\w*)\b")

#: ``total = 0``, ``count += 1``, ``dp[i] = 0`` - the left-hand side is always a
#: name the submission owns, never a library entry point.
_ASSIGNMENT_TARGET_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*"
    r"(?:\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=|=)(?!=)"
)

#: Words that are types/qualifiers rather than declared names inside a segment.
_SEGMENT_SKIP = frozenset({"std", "const", "unsigned", "signed", "long", "short",
                           "struct", "class", "enum", "typename", "auto"})

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default
    return value if value > 0 else default


#: Structural token streams are truncated to this many tokens before they are
#: compared, which bounds the worst-case cost of the pairwise pass.
MAX_STRUCTURE_TOKENS: int = _env_int("PLAGIARISM_MAX_TOKENS", 4000)

#: Default escalation threshold for ``combined_similarity``.
DEFAULT_THRESHOLD: float = 0.85

#: A student at or above this structural similarity to the teacher's reference
#: solution is considered "writing the model answer", not "copying a classmate".
TEXTBOOK_REF_SIMILARITY: float = 0.85

#: Even a textbook-shaped pair is escalated when the raw sources (identifiers
#: included) match at or above this level: shared non-standard naming is not
#: explained by a shared model answer any more.
RAW_TOKEN_ESCALATION: float = 0.92

#: Weights of the structural blend. Sequence alignment dominates because a
#: reordered-but-identical algorithm is still the same submission.
_SEQUENCE_WEIGHT: float = 0.6
_NGRAM_WEIGHT: float = 0.4

#: Size of the shingle used for the cheap containment pre-filter and the Jaccard
#: blend. Three tokens is long enough to be specific, short enough to survive
#: small edits.
_NGRAM_SIZE: int = 3


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


#: Library names from :data:`CPP_RESERVED_AND_STL` that students reuse as
#: variable names constantly. They stay in the shared vocabulary only while the
#: submission never treats them as a name of its own.
_DECLARED_AMBIGUOUS = frozenset(
    {
        "count", "size", "length", "data", "find", "begin", "end", "at", "top",
        "front", "back", "empty", "clear", "swap", "min", "max", "abs", "push",
        "pop", "insert", "erase", "resize", "substr", "first", "second",
    }
)


def _student_owned_names(cleaned: str) -> Set[str]:
    """Identifiers this submission declared or wrote to, i.e. the student's own.

    :data:`CPP_RESERVED_AND_STL` keeps well-known library names verbatim so that
    "both students reached for a ``priority_queue``" still counts as structural
    agreement. But ``count``, ``size``, ``data``, ``find`` and ``max`` are also
    among the most popular *variable* names in student code, and pinning those
    breaks the whole scheme: one student writes ``int count``, another writes
    ``int qty``, and the positional renaming that is supposed to make the two
    streams line up no longer does.

    A name that the submission declares, extracts into, or assigns to cannot be
    the library's, so it is always renamed positionally. Over-collecting is
    harmless - it only moves a name from "shared vocabulary" to "student name",
    which is applied to both submissions.
    """
    owned: Set[str] = set()
    for match in _EXTRACTION_TARGET_RE.finditer(cleaned):
        owned.add(match.group(1) or match.group(2))
    for match in _ASSIGNMENT_TARGET_RE.finditer(cleaned):
        owned.add(match.group(1))
    for match in _DECLARATION_TYPE_RE.finditer(cleaned):
        end = cleaned.find(";", match.end())
        segment = cleaned[match.end() : (end if end != -1 else match.end() + 120)]
        for name in _NAME_RE.findall(segment):
            if name in _SEGMENT_SKIP:
                continue
            if name not in CPP_RESERVED_AND_STL or name in _DECLARED_AMBIGUOUS:
                owned.add(name)
    return owned


def normalize_cpp_structure(code: str) -> str:
    """Reduce C/C++ source to a rename-resistant structural token stream.

    The pipeline is: strip comments and string literals, drop ``#include``
    lines, rewrite ``for (; cond; )`` as ``while (cond)``, tokenise, then rename
    every identifier to a positional placeholder - except language keywords and
    library vocabulary the submission did not declare itself (see
    :func:`_student_owned_names`). Two submissions that differ only in variable
    naming, whitespace, comments and include order produce byte-identical output.

    Args:
        code: raw submission source text (``""`` / ``None`` is allowed).

    Returns:
        The space-separated token stream (empty string for empty input).
    """
    if not code:
        return ""

    cleaned = strip_all_comments_and_strings(code, strip_comments=True, strip_strings=True)
    cleaned = _INCLUDE_RE.sub("", cleaned)
    cleaned = _FOR_AS_WHILE_RE.sub(r"while (\1)", cleaned)

    var_map: Dict[str, str] = {}
    normalised: List[str] = []
    owned = _student_owned_names(cleaned)
    for token in _TOKEN_RE.findall(cleaned):
        if not _NAME_RE.fullmatch(token):
            # Numeric literal or operator: structural, keep verbatim.
            normalised.append(token)
            continue
        if token in CPP_RESERVED_AND_STL and token not in owned:
            normalised.append(token)
            continue
        placeholder = var_map.get(token)
        if placeholder is None:
            placeholder = f"VAR_{len(var_map) + 1}"
            var_map[token] = placeholder
        normalised.append(placeholder)

    return " ".join(normalised)


def _structure_tokens(code: str) -> List[str]:
    """Normalised structural stream, truncated to :data:`MAX_STRUCTURE_TOKENS`."""
    tokens = normalize_cpp_structure(code).split()
    return tokens[:MAX_STRUCTURE_TOKENS]


def _raw_tokens(code: str) -> List[str]:
    """Comment-stripped source tokenised with the **real** identifier names."""
    cleaned = strip_all_comments_and_strings(
        code, strip_comments=True, strip_strings=False
    ).strip()
    return cleaned.split()[:MAX_STRUCTURE_TOKENS]


# --------------------------------------------------------------------------- #
# Similarity primitives
# --------------------------------------------------------------------------- #


def _ngrams(tokens: Sequence[str], size: int = _NGRAM_SIZE) -> Set[tuple]:
    """Set of ``size``-token shingles (deduplicated, so repetition is ignored)."""
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def _jaccard(left: Set[tuple], right: Set[tuple]) -> float:
    """``|A n B| / |A u B|`` over shingle sets (``0.0`` when both are empty)."""
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _sequence_ratio(left: Sequence[str], right: Sequence[str]) -> float:
    """Ordered-match ratio of two token streams, cheap upper bounds first.

    ``quick_ratio`` / ``real_quick_ratio`` are length- and multiset-based
    bounds; rejecting pairs they already rule out skips the O(n*m) alignment for
    the overwhelming majority of pairs in a large class.
    """
    if not left or not right:
        return 0.0
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < 0.05 or matcher.quick_ratio() < 0.05:
        return 0.0
    return matcher.ratio()


def structural_similarity(code_a: str, code_b: str) -> Dict[str, float]:
    """Blend of sequence alignment and 3-gram Jaccard for two sources.

    Returns ``{"sequence_similarity": ..., "ngram_jaccard": ...,
    "combined_similarity": ...}``, each rounded to four decimals.
    """
    left = _structure_tokens(code_a)
    right = _structure_tokens(code_b)
    sequence = _sequence_ratio(left, right)
    jaccard = _jaccard(_ngrams(left), _ngrams(right))
    return {
        "sequence_similarity": round(sequence, 4),
        "ngram_jaccard": round(jaccard, 4),
        "combined_similarity": round(
            _SEQUENCE_WEIGHT * sequence + _NGRAM_WEIGHT * jaccard, 4
        ),
    }


def reference_similarity(code: str, ref_code: str) -> float:
    """Structural similarity between ``code`` and the teacher's model answer."""
    if not ref_code or not ref_code.strip() or not code or not code.strip():
        return 0.0
    return round(_sequence_ratio(_structure_tokens(code), _structure_tokens(ref_code)), 4)


# --------------------------------------------------------------------------- #
# Pairwise screening
# --------------------------------------------------------------------------- #


def compute_similarity_pairs(
    student_codes: Mapping[str, str],
    ref_code: str = "",
    threshold: float = DEFAULT_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Compare every pair of submissions and return the suspicious ones.

    Args:
        student_codes: ``{student_key: source_text}``. The key is whatever the
            caller wants to see in the report - a username, an anonymous
            ``S07``, or ``"student 12 (sub 34)"``; it is never parsed.
        ref_code: the teacher's reference solution (``Problem.ref_code``).
            Empty disables Reference Baseline Subtraction.
        threshold: escalation cut-off applied to ``combined_similarity``.

    Returns:
        Every pair whose ``combined_similarity >= threshold``, best first. Each
        entry carries the keys the API documents -

        ``student_a`` / ``student_b``
            the two keys from ``student_codes``, in sorted order.
        ``combined_similarity``
            ``0.6 * ast_similarity + 0.4 * ngram_jaccard``.
        ``ast_similarity``
            structural sequence alignment (renamed identifiers, comments,
            includes and ``for``/``while`` re-spelling neutralised).
        ``token_similarity``
            raw source alignment with the real identifier names kept.
        ``flagged``
            ``True`` when the pair must be reviewed; ``False`` when Reference
            Baseline Subtraction cleared it as "both wrote the model answer".

        plus the diagnostic extras ``ngram_jaccard``, ``ref_similarity_a``,
        ``ref_similarity_b`` and ``exemption``.
    """
    pairs: List[Dict[str, Any]] = []
    students = {
        key: (value or "") for key, value in (student_codes or {}).items() if value
    }
    if len(students) < 2:
        return pairs

    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD

    structure = {key: _structure_tokens(source) for key, source in students.items()}
    structure = {key: tokens for key, tokens in structure.items() if tokens}
    if len(structure) < 2:
        return pairs

    shingles = {key: _ngrams(tokens) for key, tokens in structure.items()}
    raw = {key: _raw_tokens(students[key]) for key in structure}

    # --- Reference Baseline Subtraction: how textbook-like is each student? -- #
    ref_tokens = _structure_tokens(ref_code) if ref_code and ref_code.strip() else []
    ref_similarity: Dict[str, float] = {}
    if len(ref_tokens) >= _NGRAM_SIZE:
        for key, tokens in structure.items():
            ref_similarity[key] = round(_sequence_ratio(tokens, ref_tokens), 4)

    keys = sorted(structure)
    for index, student_a in enumerate(keys):
        tokens_a = structure[student_a]
        grams_a = shingles[student_a]
        for student_b in keys[index + 1 :]:
            tokens_b = structure[student_b]
            jaccard = _jaccard(grams_a, shingles[student_b])
            # The blend can only reach `threshold` if the Jaccard term is big
            # enough; skip the O(n*m) alignment for everything else.
            if _NGRAM_WEIGHT * jaccard + _SEQUENCE_WEIGHT < threshold:
                continue

            sequence = _sequence_ratio(tokens_a, tokens_b)
            combined = round(_SEQUENCE_WEIGHT * sequence + _NGRAM_WEIGHT * jaccard, 4)
            if combined < threshold:
                continue

            token_similarity = round(_sequence_ratio(raw[student_a], raw[student_b]), 4)
            sim_a = ref_similarity.get(student_a, 0.0)
            sim_b = ref_similarity.get(student_b, 0.0)

            entry: Dict[str, Any] = {
                "student_a": student_a,
                "student_b": student_b,
                "combined_similarity": combined,
                "ast_similarity": round(sequence, 4),
                "token_similarity": token_similarity,
                "flagged": True,
                # --- diagnostics ------------------------------------------------ #
                "ngram_jaccard": round(jaccard, 4),
                "ref_similarity_a": sim_a,
                "ref_similarity_b": sim_b,
                "exemption": None,
            }

            if (
                ref_tokens
                and sim_a >= TEXTBOOK_REF_SIMILARITY
                and sim_b >= TEXTBOOK_REF_SIMILARITY
                and token_similarity < RAW_TOKEN_ESCALATION
            ):
                entry["flagged"] = False
                entry["exemption"] = (
                    "both submissions match the reference solution "
                    f"(ref similarity {sim_a:.2f} / {sim_b:.2f}) and their raw "
                    f"sources differ (token similarity {token_similarity:.2f} "
                    f"< {RAW_TOKEN_ESCALATION:.2f}): standard textbook solution"
                )

            pairs.append(entry)

    pairs.sort(key=lambda pair: pair["combined_similarity"], reverse=True)
    flagged = sum(1 for pair in pairs if pair["flagged"])
    logger.info(
        "plagiarism screen: %d submissions, %d candidate pairs, %d flagged "
        "(threshold=%.2f, reference=%s)",
        len(structure),
        len(pairs),
        flagged,
        threshold,
        "yes" if ref_tokens else "no",
    )
    return pairs
