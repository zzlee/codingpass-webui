"""Sandboxed compile-and-run execution engine for C and C++ submissions.

The module exposes a single public entry point, :func:`run_code`, which:

1. writes the submission into a private, per-run temporary directory,
2. compiles it with ``gcc`` / ``g++`` (compile failures become ``compile_error``),
3. executes the resulting binary under strict wall-clock, CPU, address-space,
   file-size and file-descriptor limits, feeding ``stdin`` through a pipe,
4. classifies the outcome as ``success`` / ``compile_error`` /
   ``time_limit_exceeded`` / ``runtime_error`` (with signal details), and
5. removes the temporary directory, guaranteeing that no process in the
   child's process group survives the call.

Everything is executed with ``argv`` lists (never ``shell=True``) and a
sanitised environment, so user code can never influence the command line.

Environment overrides
---------------------
``CODE_TIMEOUT_SECONDS``   default wall-clock limit for the run phase
``CODE_COMPILE_TIMEOUT``   compile phase wall-clock limit
``CODE_MEMORY_LIMIT_MB``   ``RLIMIT_AS`` for the running binary
``CODE_MAX_OUTPUT_BYTES``  cap on captured stdout/stderr per stream
``CODE_TMPDIR``            parent directory for the per-run sandbox dirs
"""

from __future__ import annotations

import logging
import os
import resource
import shutil
import signal as _signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("compiler")

# --------------------------------------------------------------------------- #
# Limits / configuration (overridable through the environment)
# --------------------------------------------------------------------------- #


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


#: Wall-clock limit for the *execution* phase (seconds).
DEFAULT_TIMEOUT_SECONDS: float = _env_float("CODE_TIMEOUT_SECONDS", 3.0)
#: Wall-clock limit for the *compilation* phase (seconds).
COMPILE_TIMEOUT_SECONDS: float = _env_float("CODE_COMPILE_TIMEOUT", 15.0)
#: Address-space cap for the running binary (``RLIMIT_AS``).
MEMORY_LIMIT_BYTES: int = _env_int("CODE_MEMORY_LIMIT_MB", 256) * 1024 * 1024
#: Largest writable file the submission may create (``RLIMIT_FSIZE``).
FILE_SIZE_LIMIT_BYTES: int = _env_int("CODE_FILE_SIZE_LIMIT_MB", 8) * 1024 * 1024
#: Largest open-file table size for the submission (``RLIMIT_NOFILE``).
NOFILE_LIMIT: int = _env_int("CODE_NOFILE_LIMIT", 256)
#: Maximum captured bytes per output stream before truncation kicks in.
MAX_OUTPUT_BYTES: int = _env_int("CODE_MAX_OUTPUT_BYTES", 256 * 1024)


class Status:
    """Terminal states reported by :func:`run_code`."""

    SUCCESS = "success"
    COMPILE_ERROR = "compile_error"
    TIME_LIMIT_EXCEEDED = "time_limit_exceeded"
    RUNTIME_ERROR = "runtime_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class LanguageSpec:
    """How a language is compiled."""

    name: str
    compiler: str
    source_name: str
    binary_name: str = "solution"
    flags: Tuple[str, ...] = ()
    libs: Tuple[str, ...] = ()

    @property
    def version_flags(self) -> Tuple[str, ...]:
        return ("--version",)

    def compile_argv(self, source_path: str, binary_path: str) -> List[str]:
        """Build the compiler command line (argument list, never a shell string)."""
        return [
            self.compiler,
            *self.flags,
            source_path,
            "-o",
            binary_path,
            *self.libs,
        ]


#: Supported languages. Keys are the public API identifiers.
LANGUAGES: Dict[str, LanguageSpec] = {
    "c": LanguageSpec(
        name="c",
        compiler=os.environ.get("CODE_GCC", "gcc"),
        source_name="solution.c",
        flags=("-O2", "-Wall"),
        libs=("-lm",),
    ),
    "cpp": LanguageSpec(
        name="cpp",
        compiler=os.environ.get("CODE_GXX", "g++"),
        source_name="solution.cpp",
        flags=("-O2", "-std=c++17", "-Wall"),
        libs=("-lm",),
    ),
}


def supported_languages() -> List[str]:
    """Return the language identifiers accepted by :func:`run_code`."""
    return sorted(LANGUAGES)


def get_language_spec(language: str) -> LanguageSpec:
    """Resolve a language identifier, raising :class:`ValueError` when unknown."""
    spec = LANGUAGES.get((language or "").strip().lower())
    if spec is None:
        raise ValueError(
            f"unsupported language {language!r}; expected one of {supported_languages()}"
        )
    return spec


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    """Structured outcome of a compile + execute cycle."""

    status: str
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    exit_code: Optional[int] = None
    execution_time_ms: float = 0.0
    #: Human readable signal name, e.g. ``SIGSEGV`` (runtime crashes only).
    signal_name: Optional[str] = None
    #: Wall-clock compile time in milliseconds.
    compile_time_ms: float = 0.0
    #: Language identifier the result belongs to.
    language: str = ""
    #: True when the wall-clock or CPU limit was hit.
    timed_out: bool = False
    #: True when stdout and/or stderr were truncated.
    truncated: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "compile_output": self.compile_output,
            "exit_code": self.exit_code,
            "execution_time_ms": self.execution_time_ms,
            "signal_name": self.signal_name,
            "compile_time_ms": self.compile_time_ms,
            "language": self.language,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------------- #
# Internals: limits, environment, output plumbing
# --------------------------------------------------------------------------- #

_SIGNAL_HINTS = {
    _signal.SIGSEGV: "segmentation fault (invalid memory access)",
    _signal.SIGBUS: "bus error (unaligned or nonexistent physical address)",
    _signal.SIGABRT: "aborted (uncaught C++ exception, assert() failure, or failed allocation)",
    _signal.SIGFPE: "arithmetic error (division by zero or overflow)",
    _signal.SIGILL: "illegal instruction",
    _signal.SIGSYS: "blocked system call (seccomp violation)",
    _signal.SIGXCPU: "CPU time limit exceeded",
    _signal.SIGXFSZ: "file size limit exceeded",
    _signal.SIGPIPE: "broken pipe",
}

# Signals that mean "the judge stopped you" rather than "your code crashed".
_TIME_LIMIT_SIGNALS = {_signal.SIGXCPU, _signal.SIGKILL, _signal.SIGALRM}

_MEMORY_MARKERS = (
    "std::bad_alloc",
    "bad allocation",
    "Cannot allocate memory",
    "allocation memory",
    "out of memory",
    "OutOfMemoryError",
    "Mmap::Map::allocate",
)


def _setrlimit(kind: int, soft: int, hard: Optional[int] = None) -> None:
    """Best-effort ``setrlimit`` that never aborts the child on failure."""
    try:
        resource.setrlimit(kind, (soft, hard if hard is not None else soft))
    except (OSError, ValueError) as exc:  # pragma: no cover - kernel/container dependent
        logger.debug("setrlimit(%s, %s) failed: %s", kind, soft, exc)


def _make_preexec(timeout_seconds: float, *, apply_memory_limit: bool):
    """Return a ``preexec_fn`` that isolates the child in its own process group.

    The child is moved into a new session (``os.setsid``) so that the whole
    process group can be killed on timeout, which prevents runaway or zombie
    descendants. Resource limits are enforced by the kernel, so they still hold
    even if the submission bypasses our own timers with extra threads or
    processes. The working directory stays the per-run sandbox (set through
    ``Popen(cwd=...)``) so any file the submission writes is discarded.

    ``apply_memory_limit`` is False for the compiler itself: ``g++`` legitimately
    maps a large address space, so capping ``RLIMIT_AS`` there would break
    perfectly valid submissions.
    """

    def _prepare() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()
        if apply_memory_limit:
            _setrlimit(resource.RLIMIT_AS, MEMORY_LIMIT_BYTES)
        _setrlimit(resource.RLIMIT_CORE, 0)
        _setrlimit(resource.RLIMIT_FSIZE, FILE_SIZE_LIMIT_BYTES)
        _setrlimit(resource.RLIMIT_NOFILE, NOFILE_LIMIT)
        cpu = int(timeout_seconds) + 1
        _setrlimit(resource.RLIMIT_CPU, cpu, cpu + 1)

    return _prepare


def _clean_env(workdir: str) -> Dict[str, str]:
    """A minimal environment so host variables cannot leak into the sandbox."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _read_capped(stream, chunks: List[bytes], state: Dict[str, int]) -> None:
    """Drain ``stream`` into ``chunks``, keeping at most ``MAX_OUTPUT_BYTES``.

    The stream is *always* drained to EOF: dropping bytes without reading them
    would let a chatty program block on a full pipe and look like a hang.
    """
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if state["dropped"]:
                continue
            remaining = MAX_OUTPUT_BYTES - state["kept"]
            if remaining <= 0:
                state["dropped"] = 1
                continue
            if len(chunk) > remaining:
                state["dropped"] = 1
                chunk = chunk[:remaining]
            chunks.append(chunk)
            state["kept"] += len(chunk)
    except OSError:
        pass  # stream closed after a timeout kill
    finally:
        try:
            stream.close()
        except OSError:
            pass


@dataclass
class _ProcOutcome:
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: Optional[int] = None
    timed_out: bool = False
    elapsed_ms: float = 0.0
    truncated: bool = False
    launch_error: Optional[str] = None


def _execute(
    argv: List[str],
    *,
    workdir: str,
    stdin_bytes: Optional[bytes],
    timeout_seconds: float,
    apply_memory_limit: bool,
) -> _ProcOutcome:
    """Run ``argv`` under limits, feeding stdin and capturing capped output."""
    env = _clean_env(workdir)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            preexec_fn=_make_preexec(timeout_seconds, apply_memory_limit=apply_memory_limit),
        )
    except OSError as exc:
        return _ProcOutcome(launch_error=f"failed to launch {argv[0]}: {exc}")

    out_chunks: List[bytes] = []
    err_chunks: List[bytes] = []
    out_state = {"kept": 0, "dropped": 0}
    err_state = {"kept": 0, "dropped": 0}

    readers = [
        threading.Thread(
            target=_read_capped, args=(proc.stdout, out_chunks, out_state), daemon=True
        ),
        threading.Thread(
            target=_read_capped, args=(proc.stderr, err_chunks, err_state), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()

    def _feed_stdin() -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(stdin_bytes or b"")
        except OSError:
            pass  # program exited without reading its input
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    stdin_thread: Optional[threading.Thread] = None
    if stdin_bytes is not None:
        stdin_thread = threading.Thread(target=_feed_stdin, daemon=True)
        stdin_thread.start()

    start = time.perf_counter()
    timed_out = False
    exit_code: Optional[int] = None
    try:
        exit_code = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        try:
            exit_code = proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable is exotic on Linux
            logger.error("process group %s survived SIGKILL", proc.pid)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

    if stdin_thread is not None:
        stdin_thread.join(timeout=5.0)
    for reader in readers:
        reader.join(timeout=5.0)

    return _ProcOutcome(
        stdout=b"".join(out_chunks),
        stderr=b"".join(err_chunks),
        exit_code=exit_code,
        timed_out=timed_out,
        elapsed_ms=elapsed_ms,
        truncated=bool(out_state["dropped"] or err_state["dropped"]),
    )


def _kill_process_group(proc: "subprocess.Popen[bytes]") -> None:
    """SIGKILL the child's whole process group, then fall back to the child alone."""
    if proc.poll() is not None:
        # Already reaped: killing its (possibly recycled) pgid would be unsafe.
        return
    try:
        os.killpg(proc.pid, _signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _decode(data: bytes, truncated: bool, stream_label: str) -> str:
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += (
            f"\n[notice: {stream_label} truncated at {MAX_OUTPUT_BYTES} bytes of output]"
        )
    return text


def _signal_name(number: int) -> Optional[str]:
    try:
        return _signal.Signals(number).name
    except (ValueError, KeyError):  # pragma: no cover - exotic signal numbers
        return None


def _memory_error_suspected(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return any(marker.lower() in lowered for marker in _MEMORY_MARKERS)


def _compile(workdir: str, spec: LanguageSpec) -> Tuple[bool, str, Optional[int], float, bool]:
    """Compile the staged source. Returns (ok, output, exit_code, ms, truncated)."""
    source_path = os.path.join(workdir, spec.source_name)
    binary_path = os.path.join(workdir, spec.binary_name)
    outcome = _execute(
        spec.compile_argv(source_path, binary_path),
        workdir=workdir,
        stdin_bytes=None,
        timeout_seconds=COMPILE_TIMEOUT_SECONDS,
        apply_memory_limit=False,
    )
    output = _decode(outcome.stdout, outcome.truncated, "stdout")
    errors = _decode(outcome.stderr, outcome.truncated, "stderr")
    combined = "\n".join(part for part in (output.strip(), errors.strip()) if part)
    if outcome.launch_error:
        combined = "\n".join(part for part in (combined, outcome.launch_error) if part)

    ok = (
        outcome.exit_code == 0
        and not outcome.timed_out
        and outcome.launch_error is None
        and os.path.exists(binary_path)
    )
    if outcome.timed_out:
        combined = "\n".join(
            part
            for part in (
                combined,
                f"[error: compiler killed after {COMPILE_TIMEOUT_SECONDS:.1f}s]",
            )
            if part
        )
    return ok, combined, outcome.exit_code, outcome.elapsed_ms, outcome.truncated


def _toolchain_sandbox() -> str:
    """Parent directory for the per-run sandboxes (defaults to ``/tmp``)."""
    root = os.environ.get("CODE_TMPDIR") or tempfile.gettempdir()
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def run_code(
    code: str,
    language: str,
    stdin_data: Optional[str] = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RunResult:
    """Compile ``code`` for ``language`` and run it against ``stdin_data``.

    Never raises for bad user input: problems are reported through ``status``
    (``compile_error`` / ``time_limit_exceeded`` / ``runtime_error`` /
    ``internal_error``) so the HTTP layer can answer with a uniform payload.

    Args:
        code: the submission source text.
        language: ``"c"`` or ``"cpp"``.
        stdin_data: bytes fed to the program on stdin (``None`` -> no stdin).
        timeout_seconds: wall-clock limit for the execution phase.

    Returns:
        A :class:`RunResult` describing status, captured output, exit code and
        measured execution time.
    """
    try:
        spec = get_language_spec(language)
    except ValueError as exc:
        return RunResult(status=Status.INTERNAL_ERROR, stderr=str(exc), language=str(language))

    source_bytes = (code or "").encode("utf-8", errors="replace")
    compiler_path = shutil.which(spec.compiler)
    if compiler_path is None:
        return RunResult(
            status=Status.INTERNAL_ERROR,
            language=spec.name,
            stderr=f"{spec.compiler} was not found on the server PATH",
        )

    workdir = tempfile.mkdtemp(prefix=f"cj-{spec.name}-", dir=_toolchain_sandbox())
    try:
        try:
            os.chmod(workdir, 0o700)
        except OSError:  # pragma: no cover
            pass

        source_path = os.path.join(workdir, spec.source_name)
        with open(source_path, "wb") as handle:
            handle.write(source_bytes)
        try:
            os.chmod(source_path, 0o600)
        except OSError:  # pragma: no cover
            pass

        ok, compile_output, compile_rc, compile_ms, compile_trunc = _compile(workdir, spec)
        if not ok:
            return RunResult(
                status=Status.COMPILE_ERROR,
                compile_output=compile_output,
                exit_code=compile_rc,
                compile_time_ms=round(compile_ms, 3),
                execution_time_ms=0.0,
                language=spec.name,
                truncated=compile_trunc,
            )

        binary_path = os.path.join(workdir, spec.binary_name)
        try:
            os.chmod(binary_path, 0o700)
        except OSError:  # pragma: no cover
            pass

        stdin_bytes = None if stdin_data is None else (stdin_data or "").encode("utf-8")
        outcome = _execute(
            [binary_path],
            workdir=workdir,
            stdin_bytes=stdin_bytes,
            timeout_seconds=timeout_seconds,
            apply_memory_limit=True,
        )

        if outcome.launch_error is not None:
            return RunResult(
                status=Status.INTERNAL_ERROR,
                compile_output=compile_output,
                stderr=outcome.launch_error,
                compile_time_ms=round(compile_ms, 3),
                language=spec.name,
            )

        stdout_text = _decode(outcome.stdout, outcome.truncated, "stdout")
        stderr_text = _decode(outcome.stderr, outcome.truncated, "stderr")
        status, signal_name, notes = _classify(outcome, stderr_text, timeout_seconds)
        if notes:
            stderr_text = "\n".join(part for part in (stderr_text.rstrip("\n"), notes) if part)

        return RunResult(
            status=status,
            stdout=stdout_text,
            stderr=stderr_text,
            compile_output=compile_output,
            exit_code=outcome.exit_code,
            execution_time_ms=round(outcome.elapsed_ms, 3),
            signal_name=signal_name,
            compile_time_ms=round(compile_ms, 3),
            language=spec.name,
            timed_out=outcome.timed_out or status == Status.TIME_LIMIT_EXCEEDED,
            truncated=outcome.truncated or compile_trunc,
        )
    except Exception as exc:  # defensive: never leak a 500 from user input
        logger.exception("unexpected failure while executing a %s submission", spec.name)
        return RunResult(
            status=Status.INTERNAL_ERROR,
            language=spec.name,
            stderr=f"internal error while executing submission: {exc.__class__.__name__}: {exc}",
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _classify(
    outcome: _ProcOutcome, stderr_text: str, timeout_seconds: float
) -> Tuple[str, Optional[str], str]:
    """Map a finished process onto a judge status plus an explanatory note."""
    if outcome.timed_out:
        return (
            Status.TIME_LIMIT_EXCEEDED,
            None,
            f"[error: time limit of {timeout_seconds:.1f}s exceeded; process group was killed]",
        )

    code = outcome.exit_code
    if code is None:
        return (
            Status.RUNTIME_ERROR,
            None,
            "[error: the program could not be reaped]",
        )

    if code < 0:  # killed by a signal
        number = -code
        name = _signal_name(number)
        hint = _SIGNAL_HINTS.get(number) if name else None
        if name in _TIME_LIMIT_SIGNALS:
            return (
                Status.TIME_LIMIT_EXCEEDED,
                name,
                f"[error: terminated by {name} - CPU time limit of "
                f"{int(timeout_seconds) + 1}s exceeded]",
            )
        if name == "SIGABRT" and _memory_error_suspected(stderr_text):
            return (
                Status.RUNTIME_ERROR,
                name,
                f"[error: {name} ({hint}) - memory limit of "
                f"{MEMORY_LIMIT_BYTES // (1024 * 1024)} MB exceeded]",
            )
        detail = f"{name} (signal {number})" if name else f"signal {number}"
        if hint:
            detail = f"{detail} - {hint}"
        memory_note = ""
        if _memory_error_suspected(stderr_text):
            memory_note = " (looks like a memory limit violation)"
        return (
            Status.RUNTIME_ERROR,
            name,
            f"[error: terminated by {detail}{memory_note}]",
        )

    if code != 0:
        note = f"[error: program exited with code {code}]"
        if _memory_error_suspected(stderr_text):
            note += (
                f" - memory limit of {MEMORY_LIMIT_BYTES // (1024 * 1024)} MB "
                "was probably exceeded"
            )
        return Status.RUNTIME_ERROR, None, note

    return Status.SUCCESS, None, ""


_VERSION_CACHE: Dict[str, str] = {}
_VERSION_LOCK = threading.Lock()


def compiler_version(compiler: str) -> str:
    """Return the first ``--version`` line of a compiler (or an error string)."""
    with _VERSION_LOCK:
        cached = _VERSION_CACHE.get(compiler)
    if cached is not None:
        return cached

    path = shutil.which(compiler)
    if path is None:
        version = f"{compiler} not available"
    else:
        try:
            completed = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=_clean_env(os.getcwd()),
            )
            first_line = (completed.stdout or completed.stderr or "").splitlines()
            version = first_line[0].strip() if first_line else f"{compiler} (no version output)"
        except (OSError, subprocess.SubprocessError) as exc:
            version = f"{compiler} unavailable: {exc}"

    with _VERSION_LOCK:
        _VERSION_CACHE[compiler] = version
    return version


def toolchain_versions() -> Dict[str, str]:
    """Return ``{"gcc": ..., "g++": ...}`` version strings for the health probe."""
    return {
        LANGUAGES["c"].compiler: compiler_version(LANGUAGES["c"].compiler),
        LANGUAGES["cpp"].compiler: compiler_version(LANGUAGES["cpp"].compiler),
    }


def limits_summary() -> Dict[str, object]:
    """Expose the active sandbox limits (handy for diagnostics)."""
    return {
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "compile_timeout_seconds": COMPILE_TIMEOUT_SECONDS,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        "file_size_limit_bytes": FILE_SIZE_LIMIT_BYTES,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "languages": supported_languages(),
    }
