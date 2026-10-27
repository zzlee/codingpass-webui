"""TeDa-Testing random-input engine and dual-run reference judging.

Ported from ``codingpass-tests/tools/cp_sandbox.py`` and reshaped for the
FastAPI backend: everything works on in-memory strings (the pieces live in
``Problem.teda_random_script`` / ``Problem.ref_code``), no pack files are
touched here.

Public API
----------
:func:`transpile_teda_to_js`
    Deterministic TeDa -> JavaScript transpiler (same structural subset as the
    offline sandbox: ``expr;`` implicit prints, silent ``@expr;``, ``n;`` /
    ``s;`` shorthands, ``[count;fmt]{ }`` loops, ``P(w){ }`` weighted
    branches, ``A~B[:step]`` ranges, and nested JS control flow).
:func:`generate_random_inputs`
    Execute the transpiled script ``num_random`` times through ``node -e``
    with a deterministic per-case seed and return the generated stdin texts.
:func:`compare_outputs`
    Strict vs trailing-whitespace-trimmed comparison (the TeDa ``trimEnd``
    judge semantics), reporting presentation warnings separately.
:func:`run_dual_run`
    Compile the teacher's reference solution once in a private temporary
    directory and execute it once per stdin, returning the reference stdout
    for every case (``ans_01, ans_02, ...``). Compilation and every execution
    reuse the hardened :mod:`compiler` sandbox (rlimits, process-group kill,
    capped output), so a buggy reference can never hurt the host.

Environment overrides
---------------------
``TEDA_NODE``           node binary name/path (default ``node``)
``TEDA_NODE_TIMEOUT``   wall-clock limit per generator run, seconds (default 10)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Tuple

try:  # package-style launch: `uvicorn backend.main:app`
    from . import compiler as _compiler
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    import compiler as _compiler  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.teda")

__all__ = [
    "transpile_teda_to_js",
    "generate_random_inputs",
    "compare_outputs",
    "run_dual_run",
    "TeDaError",
    "NodeUnavailableError",
    "TeDaExecutionError",
    "DualRunError",
]

# --------------------------------------------------------------------------- #
# Configuration / errors
# --------------------------------------------------------------------------- #

#: Node binary used to execute transpiled TeDa scripts.
NODE_BINARY: str = os.environ.get("TEDA_NODE", "node")


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default
    return value if value > 0 else default


#: Wall-clock limit for one ``node -e`` generator execution (seconds).
NODE_TIMEOUT_SECONDS: float = _env_float("TEDA_NODE_TIMEOUT", 10.0)


class TeDaError(RuntimeError):
    """Base class for every TeDa engine failure."""


class NodeUnavailableError(TeDaError):
    """The ``node`` binary required to run transpiled scripts is missing."""


class TeDaExecutionError(TeDaError):
    """The transpiled script exited non-zero or misbehaved."""


class DualRunError(RuntimeError):
    """The teacher's reference solution failed to compile or run cleanly.

    :attr:`details` carries the compiler stderr or the runtime diagnosis so the
    API layer can tell the teacher *why* the pack is broken.
    """

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message if not details else f"{message}: {details[:2000]}")
        self.details = details


# --------------------------------------------------------------------------- #
# Structural JS scanning helpers (quote / bracket aware)
# --------------------------------------------------------------------------- #


def _skip_quoted(src: str, i: int) -> int:
    """Return the index just past the quoted string literal starting at src[i]."""
    quote = src[i]
    i += 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        if quote != "`" and ch == "\n":
            return i + 1
        i += 1
    return i


def _skip_template(src: str, i: int) -> int:
    """Return the index just past the template literal starting at src[i] (handles ${...})."""
    i += 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            return i + 1
        if ch == "$" and i + 1 < n and src[i + 1] == "{":
            i = _match_bracket(src, i + 1)
            continue
        i += 1
    return i


_TEDA_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _match_bracket(src: str, i: int) -> int:
    """Return the index just past the bracket matching src[i] (one of ( [ {)."""
    if i >= len(src) or src[i] not in _TEDA_OPEN_BRACKETS:
        raise TeDaError(
            f"Expected an opening bracket at offset {i}, got {src[i:i + 1]!r}"
        )
    expected = [_TEDA_OPEN_BRACKETS[src[i]]]
    i += 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            i = _skip_quoted(src, i)
            continue
        if ch == "`":
            i = _skip_template(src, i)
            continue
        if ch in _TEDA_OPEN_BRACKETS:
            expected.append(_TEDA_OPEN_BRACKETS[ch])
            i += 1
            continue
        if ch in ")]}":
            if ch != expected[-1]:
                raise TeDaError(
                    f"Unbalanced bracket: expected {expected[-1]!r}, got {ch!r}"
                )
            expected.pop()
            i += 1
            if not expected:
                return i
            continue
        i += 1
    raise TeDaError(f"Unbalanced bracket: missing {expected[-1]!r}")


def _find_statement_end(src: str, i: int) -> int:
    """Return the index of the top-level ';' terminating the statement at i (or len(src))."""
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            i = _skip_quoted(src, i)
            continue
        if ch == "`":
            i = _skip_template(src, i)
            continue
        if ch in _TEDA_OPEN_BRACKETS:
            i = _match_bracket(src, i)
            continue
        if ch == ";":
            return i
        i += 1
    return n


def _skip_ws(src: str, i: int) -> int:
    n = len(src)
    while i < n and src[i].isspace():
        i += 1
    return i


def _skip_ws_and_semicolons(src: str, i: int) -> int:
    n = len(src)
    while i < n and (src[i].isspace() or src[i] == ";"):
        i += 1
    return i


def _split_top_level(src: str, delim: str) -> List[str]:
    parts: List[str] = []
    start = 0
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            i = _skip_quoted(src, i)
            continue
        if ch == "`":
            i = _skip_template(src, i)
            continue
        if ch in _TEDA_OPEN_BRACKETS:
            i = _match_bracket(src, i)
            continue
        if ch == delim:
            parts.append(src[start:i])
            start = i + 1
        i += 1
    parts.append(src[start:])
    return parts


def _find_top_level_open_brace(src: str, i: int) -> int:
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            i = _skip_quoted(src, i)
            continue
        if ch == "`":
            i = _skip_template(src, i)
            continue
        if ch in "([":
            i = _match_bracket(src, i)
            continue
        if ch == "{":
            return i
        i += 1
    return -1


# --------------------------------------------------------------------------- #
# TeDa statement translation
# --------------------------------------------------------------------------- #


def _format_char_code(ch: str) -> str:
    """Map a TeDa format character to the literal it emits."""
    if ch == "n":
        return "\n"
    if ch == "s":
        return " "
    return ""


def _js_str(text: str) -> str:
    return json.dumps(text)


def _parse_loop_format(fmt: str) -> Tuple[bool, str, str, str]:
    """
    Decompose a TeDa loop format string such as enn / ene / esn / csse into
    (count_prefixed, leading, between, trailing).

    The official layout is [首][項間][尾]; a leading ``c`` additionally prints
    the element count before the items.
    """
    fmt = fmt.strip().lower()
    count_prefixed = "c" in fmt
    fmt = fmt.replace("c", "")
    if len(fmt) == 0:
        return count_prefixed, "", "", ""
    trailing = _format_char_code(fmt[-1])
    between = _format_char_code(fmt[-2]) if len(fmt) >= 2 else ""
    leading = "".join(_format_char_code(ch) for ch in fmt[:-2])
    return count_prefixed, leading, between, trailing


def _transform_teda_body(src: str) -> str:
    """Translate TeDa-Testing statements into JavaScript that appends to the active buffer."""
    out: List[str] = []
    n = len(src)
    i = 0
    while True:
        i = _skip_ws_and_semicolons(src, i)
        if i >= n:
            break

        # Weighted probability branch(es): P(w){ ... }
        if re.match(r"P\s*\(", src[i:]):
            block, i = _parse_p_group(src, i)
            out.append(block)
            continue

        # Repeat loop: [count;fmt]{ ... }
        if src[i] == "[":
            block, i = _parse_loop(src, i)
            out.append(block)
            continue

        # Bare block statement: { ... }
        if src[i] == "{":
            end = _match_bracket(src, i)
            out.append("{\n" + _transform_teda_body(src[i + 1:end - 1]) + "\n}")
            i = end
            continue

        # Silent statement: @expr;
        if src[i] == "@":
            j = _find_statement_end(src, i + 1)
            stmt = src[i + 1:j].strip()
            if stmt:
                out.append(stmt + ";")
            i = j + 1
            continue

        # Control-flow constructs (bodies are recursively transformed).
        m = re.match(r"(if|for|while|switch|do|try)\b", src[i:])
        if m:
            block, i = _parse_control(src, i, m.group(1))
            out.append(block)
            continue

        # Declarations / function definitions are executed but never printed.
        m = re.match(
            r"(let|const|var|function|class|return|break|continue)\b", src[i:]
        )
        if m:
            if m.group(1) in ("function", "class"):
                brace = _find_top_level_open_brace(src, i)
                end = (
                    _match_bracket(src, brace)
                    if brace != -1
                    else _find_statement_end(src, i)
                )
                out.append(src[i:end].strip())
                i = end
                continue
            j = _find_statement_end(src, i)
            stmt = src[i:j].strip()
            if stmt:
                out.append(stmt + ";")
            i = j + 1
            continue

        # Statement-level statement: expr; / n; / s(k); ...
        j = _find_statement_end(src, i)
        raw = src[i:j].strip()

        # TeDa output shorthands: n -> newline, s -> space, n(k) / s(k) repeated.
        shorthand = re.fullmatch(r"([ns])\s*(?:\(\s*(\d+)\s*\))?", raw)
        if shorthand:
            unit = _js_str("\n" if shorthand.group(1) == "n" else " ")
            times = shorthand.group(2)
            expr = unit if not times else f"{unit}.repeat({times})"
            out.append(f"__emit({expr});")
            i = j + 1
            continue

        # Any other expression statement is TeDa's implicit "print this value".
        if raw:
            out.append(f"__emit(String({raw}));")
        i = j + 1
    return "\n".join(out)


def _parse_p_group(src: str, i: int) -> Tuple[str, int]:
    """Parse one or more consecutive P(weight){ body } blocks into a weighted choice."""
    branches: List[Tuple[str, str]] = []
    n = len(src)
    while True:
        m = re.match(r"P\s*\(", src[i:])
        if not m:
            break
        paren = i + m.end() - 1
        paren_end = _match_bracket(src, paren)
        weight = src[paren + 1:paren_end - 1].strip()
        brace = _skip_ws(src, paren_end)
        if brace >= n or src[brace] != "{":
            raise TeDaError(f"Malformed P({weight}) block: expected '{{'")
        brace_end = _match_bracket(src, brace)
        body = src[brace + 1:brace_end - 1]
        branches.append((weight, body))
        i = _skip_ws(src, brace_end)
        if i < n and src[i] == ";":
            i = _skip_ws(src, i + 1)

    try:
        weights = [float(w) for w, _ in branches]
    except ValueError as exc:
        raise TeDaError(f"Unsupported non-numeric P weight in {branches!r}") from exc

    total = sum(weights) or 1.0
    lines = ["{", f"  let __p = __rng() * {total!r};"]
    cumulative = 0.0
    for idx, (_weight, body) in enumerate(branches):
        cumulative += weights[idx]
        if idx == len(branches) - 1:
            lines.append("  else {")
        elif idx == 0:
            lines.append(f"  if (__p < {cumulative!r}) {{")
        else:
            lines.append(f"  else if (__p < {cumulative!r}) {{")
        lines.append(_transform_teda_body(body))
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines), i


def _parse_loop(src: str, i: int) -> Tuple[str, int]:
    """Parse a TeDa repeat loop [count;fmt]{ body } and emit a JS item-collecting loop."""
    bracket_end = _match_bracket(src, i)
    header = src[i + 1:bracket_end - 1]
    parts = _split_top_level(header, ";")
    if len(parts) < 2:
        raise TeDaError(f"Malformed TeDa loop header: [{header}]")
    count_expr = parts[0].strip()
    fmt = parts[1].strip()

    brace = _skip_ws(src, bracket_end)
    if brace >= len(src) or src[brace] != "{":
        raise TeDaError(f"Malformed TeDa loop [{header}]: expected '{{'")
    brace_end = _match_bracket(src, brace)
    body = src[brace + 1:brace_end - 1]

    count_prefixed, leading, between, trailing = _parse_loop_format(fmt)
    prefix = _js_str(leading)
    if count_prefixed:
        prefix = f"String(__count) + {prefix}"

    code = "\n".join(
        [
            "{",
            f"  const __count = {count_expr};",
            "  const __items = [];",
            "  for (let __i = 0; __i < __count; __i++) {",
            '    __stack.push("");',
            _transform_teda_body(body),
            "    __items.push(__stack.pop());",
            "  }",
            f"  __emit({prefix} + __items.join({_js_str(between)}) + {_js_str(trailing)});",
            "}",
        ]
    )

    end = _skip_ws(src, brace_end)
    if end < len(src) and src[end] == ";":
        end += 1
    return code, end


def _parse_if_chain(src: str, i: int) -> Tuple[str, int]:
    """Parse if / else-if / else chains, recursively transforming each body."""
    j = _skip_ws(src, i + 2)
    if j >= len(src) or src[j] != "(":
        raise TeDaError("Malformed if statement: missing '('")
    paren_end = _match_bracket(src, j)
    cond = src[j + 1:paren_end - 1]

    branch = _skip_ws(src, paren_end)
    if branch < len(src) and src[branch] == "{":
        end = _match_bracket(src, branch)
        body = src[branch + 1:end - 1]
        code = f"if ({cond}) {{\n{_transform_teda_body(body)}\n}}"
    else:
        end = _find_statement_end(src, branch)
        code = f"if ({cond}) {{\n{_transform_teda_body(src[branch:end])}\n}}"
        end += 1

    k = _skip_ws(src, end)
    if src.startswith("else", k) and not re.match(r"\w", src[k + 4:k + 5]):
        k = _skip_ws(src, k + 4)
        if src.startswith("if", k) and not re.match(r"\w", src[k + 2:k + 3]):
            nested, end = _parse_if_chain(src, k)
            code += " else " + nested
        elif k < len(src) and src[k] == "{":
            end = _match_bracket(src, k)
            code += " else {\n" + _transform_teda_body(src[k + 1:end - 1]) + "\n}"
        else:
            end = _find_statement_end(src, k)
            code += " else {\n" + _transform_teda_body(src[k:end]) + "\n}"
            end += 1
    return code, end


def _parse_control(src: str, i: int, keyword: str) -> Tuple[str, int]:
    """Parse a JS control-flow statement, transforming any block bodies it contains."""
    if keyword == "if":
        return _parse_if_chain(src, i)

    j = _skip_ws(src, i + len(keyword))
    brace = _find_top_level_open_brace(src, j)
    if brace == -1:
        end = _find_statement_end(src, i)
        return src[i:end], end + 1

    head = src[i:brace]
    end = _match_bracket(src, brace)
    body = src[brace + 1:end - 1]
    code = head + "{\n" + _transform_teda_body(body) + "\n}"

    if keyword == "do":
        # do { ... } while (cond);
        k = _skip_ws(src, end)
        stmt_end = _find_statement_end(src, k)
        code += " " + src[k:stmt_end].strip() + ";"
        return code, stmt_end + 1

    if keyword == "try":
        # try { ... } catch (e) { ... } finally { ... }
        k = end
        while True:
            k = _skip_ws(src, k)
            clause_match = re.match(r"(catch|finally)\b", src[k:])
            if not clause_match:
                break
            k2 = _skip_ws(src, k + clause_match.end())
            if k2 < len(src) and src[k2] == "(":
                k2 = _skip_ws(src, _match_bracket(src, k2))
            if k2 >= len(src) or src[k2] != "{":
                break
            k_end = _match_bracket(src, k2)
            clause_head = src[k:k2].strip()
            code += (
                " "
                + clause_head
                + " {\n"
                + _transform_teda_body(src[k2 + 1:k_end - 1])
                + "\n}"
            )
            k = k_end
        return code, k

    return code, end


# --------------------------------------------------------------------------- #
# Public: transpiler
# --------------------------------------------------------------------------- #


def transpile_teda_to_js(teda_src: str, seed: int) -> str:
    """
    Transpile a CodingPass TeDa-Testing script into standard deterministic JavaScript (Node.js).

    Supported subset:
      - output statements: ``expr;``, silent ``@expr;``, ``n;``, ``n(k);``, ``s;``, ``s(k);``
      - ``[A~B;fmt]{ body }`` repeat loops (nested loops supported; fmt: enn/ene/esn/csse)
      - ``P(weight){ body }`` weighted probability branches
      - ``A~B`` and ``A~B:step`` random range operators

    The embedded PRNG is seeded with ``seed``, so the same script + seed always
    reproduces byte-identical input files (re-judging old submissions works).
    """
    if teda_src is None or not teda_src.strip():
        raise TeDaError("empty TeDa script: nothing to transpile")

    # 1. Strip comments
    src = re.sub(r"//[^\n]*", "", teda_src)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)

    # 2. Replace A~B:step and A~B range syntax with __randRange(A, B, step)
    src = re.sub(
        r"(\b\d+\b)\s*~\s*(\b\d+\b)\s*:\s*(\b\d+\b)",
        r"__randRange(\1, \2, \3)",
        src,
    )
    src = re.sub(r"(\b\d+\b)\s*~\s*(\b\d+\b)", r"__randRange(\1, \2, 1)", src)

    # 3. Structurally translate statements (handles nested loops / P blocks / @ silence)
    body = _transform_teda_body(src)

    js_header = f"""
let __seed = {int(seed) & 0xFFFFFFFF} >>> 0;
function __rng() {{
  __seed = (__seed + 0x6D2B79F5) >>> 0;
  let t = Math.imul(__seed ^ (__seed >>> 15), 1 | __seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}}
function __randRange(a, b, step) {{
  step = step || 1;
  const count = Math.floor((b - a) / step) + 1;
  return a + Math.floor(__rng() * count) * step;
}}
const __stack = [""];
function __emit(s) {{
  __stack[__stack.length - 1] += String(s);
}}
"""
    return js_header + "\n" + body + "\n" + 'process.stdout.write(__stack[0]);\n'


# --------------------------------------------------------------------------- #
# Public: random input generation via node
# --------------------------------------------------------------------------- #


def generate_random_inputs(
    teda_src: str,
    num_random: int = 30,
    seed: int = 42,
) -> List[str]:
    """Execute a TeDa generator ``num_random`` times and return the stdin texts.

    Case ``i`` runs with the deterministic seed ``seed * 1000 + i`` (the exact
    convention of the offline sandbox), so a pack imported today reproduces the
    same 30 hidden inputs next semester.

    Raises:
        NodeUnavailableError: the ``node`` binary is not on the server PATH.
        TeDaExecutionError:   a script run timed out or exited non-zero.
        TeDaError:            the script failed to transpile.
    """
    node_path = shutil.which(NODE_BINARY)
    if node_path is None:
        raise NodeUnavailableError(
            f"{NODE_BINARY!r} was not found on the server PATH; "
            "install Node.js or set TEDA_NODE to generate TeDa test cases"
        )
    if num_random <= 0:
        return []

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }

    inputs: List[str] = []
    for idx in range(1, num_random + 1):
        js_code = transpile_teda_to_js(teda_src, seed=seed * 1000 + idx)
        try:
            completed = subprocess.run(
                [node_path, "-e", js_code],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=NODE_TIMEOUT_SECONDS,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TeDaExecutionError(
                f"TeDa execution timed out on case {idx} "
                f"(limit {NODE_TIMEOUT_SECONDS:.1f}s)"
            ) from exc
        except OSError as exc:
            raise NodeUnavailableError(
                f"failed to launch {NODE_BINARY!r} for case {idx}: {exc}"
            ) from exc

        if completed.returncode != 0:
            stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
            raise TeDaExecutionError(
                f"TeDa execution failed on case {idx}: "
                f"{stderr_text.strip()[:2000] or f'exit code {completed.returncode}'}"
            )
        inputs.append((completed.stdout or b"").decode("utf-8", errors="replace"))
    return inputs


# --------------------------------------------------------------------------- #
# Public: output comparison
# --------------------------------------------------------------------------- #


def compare_outputs(actual: str, expected: str) -> Dict[str, bool]:
    """Compare student output against reference output under two modes.

    * ``trimmed_match``: trailing spaces/tabs per line are ignored (matches the
      CodingPass TeDa ``trimEnd`` judge - the pass/fail criterion).
    * ``strict_match``:  every byte of the visible layout must agree.
    * ``presentation_warning``: the answer is judged correct, but the student
      left trailing whitespace a strict judge would have rejected (PE warning).
    """
    actual_norm = (actual or "").replace("\r\n", "\n").rstrip("\n")
    expected_norm = (expected or "").replace("\r\n", "\n").rstrip("\n")

    strict_actual_lines = actual_norm.split("\n") if actual_norm != "" else []
    strict_expected_lines = expected_norm.split("\n") if expected_norm != "" else []

    trimmed_actual_lines = [line.rstrip(" \t") for line in strict_actual_lines]
    trimmed_expected_lines = [line.rstrip(" \t") for line in strict_expected_lines]

    trimmed_match = trimmed_actual_lines == trimmed_expected_lines
    strict_match = strict_actual_lines == strict_expected_lines

    return {
        "strict_match": strict_match,
        "trimmed_match": trimmed_match,
        "presentation_warning": bool(trimmed_match and not strict_match),
    }


# --------------------------------------------------------------------------- #
# Public: dual-run reference judging
# --------------------------------------------------------------------------- #


def run_dual_run(
    ref_code: str,
    language: str,
    stdin_inputs: List[str],
    time_limit_sec: float = 3.0,
) -> List[str]:
    """Compile ``ref_code`` once and run it against every stdin, returning stdout per case.

    The reference solution is the teacher's own code (``*_庫題卡碼文標碼.cpp``);
    it is staged inside a private temporary directory and executed through the
    same sandboxed plumbing :mod:`compiler` uses for student submissions
    (``os.setsid`` + rlimits + process-group kill + capped output), so a buggy
    or malicious reference cannot harm the host either.

    Args:
        ref_code: reference source text.
        language: ``"c"`` or ``"cpp"`` (mirrors :mod:`compiler`).
        stdin_inputs: one stdin text per test case, in order.
        time_limit_sec: per-case wall-clock limit for the reference binary.

    Returns:
        The reference stdout for every input: ``[ans_01, ans_02, ...]``.

    Raises:
        ValueError:   unsupported language or empty reference source.
        DualRunError: the reference did not compile, failed to launch,
            timed out, crashed, or produced truncated output on some case.
    """
    try:
        spec = _compiler.get_language_spec(language)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    if not ref_code or not ref_code.strip():
        raise ValueError("run_dual_run requires a non-empty reference solution")
    if not stdin_inputs:
        return []

    compiler_path = shutil.which(spec.compiler)
    if compiler_path is None:
        raise DualRunError(f"{spec.compiler} was not found on the server PATH")

    workdir = tempfile.mkdtemp(prefix="cj-ref-", dir=_compiler._toolchain_sandbox())
    try:
        try:
            os.chmod(workdir, 0o700)
        except OSError:  # pragma: no cover
            pass

        source_path = os.path.join(workdir, spec.source_name)
        with open(source_path, "wb") as handle:
            handle.write(ref_code.encode("utf-8", errors="replace"))
        try:
            os.chmod(source_path, 0o600)
        except OSError:  # pragma: no cover
            pass

        ok, compile_output, _rc, _ms, _trunc = _compiler._compile(workdir, spec)
        if not ok:
            raise DualRunError(
                f"reference {spec.name.upper()} solution failed to compile",
                compile_output,
            )

        binary_path = os.path.join(workdir, spec.binary_name)
        try:
            os.chmod(binary_path, 0o700)
        except OSError:  # pragma: no cover
            pass

        outputs: List[str] = []
        for idx, stdin_text in enumerate(stdin_inputs, start=1):
            outcome = _compiler._execute(
                [binary_path],
                workdir=workdir,
                stdin_bytes=(stdin_text or "").encode("utf-8"),
                timeout_seconds=time_limit_sec,
                apply_memory_limit=True,
            )
            case = f"case {idx:02d}"
            if outcome.launch_error:
                raise DualRunError(f"reference binary failed to launch on {case}",
                                   outcome.launch_error)
            if outcome.timed_out:
                raise DualRunError(
                    f"reference solution timed out on {case}",
                    f"wall-clock limit of {time_limit_sec:.1f}s exceeded",
                )
            if outcome.exit_code != 0:
                stderr_text = _compiler._decode(
                    outcome.stderr, outcome.truncated, "stderr"
                )
                raise DualRunError(
                    f"reference solution exited abnormally on {case} "
                    f"(exit code {outcome.exit_code})",
                    stderr_text,
                )
            if outcome.truncated:
                raise DualRunError(
                    f"reference solution output on {case} was truncated at "
                    f"{_compiler.MAX_OUTPUT_BYTES} bytes; the test data is too large",
                )
            outputs.append(
                _compiler._decode(outcome.stdout, False, "stdout")
            )
        return outputs
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
