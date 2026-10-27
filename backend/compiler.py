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
``CODE_MEMORY_LIMIT_MB``   default ``RLIMIT_AS`` for the running binary; a single
                           submission may ask for a smaller budget of its own via
                           ``run_code(..., memory_limit_mb=...)`` / ``run_code_batch``
``CODE_MAX_OUTPUT_BYTES``  cap on captured stdout/stderr per stream
``CODE_NPROC_HEADROOM``    extra tasks a submission may create (fork-bomb cap)
``CODE_PIPE_DRAIN_GRACE_SEC`` drain grace after the direct child exits
``CODE_TMPDIR``            parent directory for the per-run sandbox dirs
"""

from __future__ import annotations

import logging
import os
import resource
import select
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
#: Default address-space cap for the running binary (``RLIMIT_AS``).
MEMORY_LIMIT_BYTES: int = _env_int("CODE_MEMORY_LIMIT_MB", 256) * 1024 * 1024
#: Bounds of a caller-supplied per-problem budget, in MB. ``RLIMIT_AS`` of ``0``
#: means *unlimited* to the kernel, so a nonsensical override (``0``, ``-1``,
#: ``10**9``) must never reach :func:`resource.setrlimit`: the floor keeps a
#: realistic margin for a statically linked hello-world plus libc, the ceiling
#: stops one problem's setting from disarming the sandbox for everybody else.
MEMORY_LIMIT_FLOOR_MB: int = 16
MEMORY_LIMIT_CEIL_MB: int = 8192
#: Largest writable file the submission may create (``RLIMIT_FSIZE``).
FILE_SIZE_LIMIT_BYTES: int = _env_int("CODE_FILE_SIZE_LIMIT_MB", 8) * 1024 * 1024
#: Largest open-file table size for the submission (``RLIMIT_NOFILE``).
NOFILE_LIMIT: int = _env_int("CODE_NOFILE_LIMIT", 256)
#: Maximum captured bytes per output stream before truncation kicks in.
MAX_OUTPUT_BYTES: int = _env_int("CODE_MAX_OUTPUT_BYTES", 256 * 1024)
#: How much longer the output drains may run after the direct child is gone.
#: A submission that leaves a descendant holding a pipe open must never be able
#: to block the judge, so the drains are deadline-bounded, not EOF-bounded.
PIPE_DRAIN_GRACE_SECONDS: float = _env_float("CODE_PIPE_DRAIN_GRACE_SEC", 1.0)
#: Extra tasks a submission may create above what the judge's own uid already
#: owns. ``RLIMIT_NPROC`` is per-real-uid (threads included), so an absolute
#: number would either be unreachable or break ``gcc``'s own forks; a headroom
#: measured at spawn time caps fork bombs without breaking valid submissions.
NPROC_HEADROOM: int = _env_int("CODE_NPROC_HEADROOM", 64)


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


def _resolve_memory_limit_bytes(memory_limit_mb: Optional[int]) -> int:
    """Address-space ceiling for one submission, in bytes.

    ``None`` - the default everywhere in this module - keeps the process-wide
    :data:`MEMORY_LIMIT_BYTES`, so every existing caller behaves exactly as
    before. A per-problem value (``Problem.memory_limit_mb``) is clamped into
    :data:`MEMORY_LIMIT_FLOOR_MB`..:data:`MEMORY_LIMIT_CEIL_MB` instead of being
    trusted verbatim: the value reaches the route layer from a teacher's form, and
    an unclamped ``0`` would mean "no memory limit at all" to the kernel rather
    than "no memory allowed".
    """
    if memory_limit_mb is None:
        return MEMORY_LIMIT_BYTES
    try:
        requested = int(memory_limit_mb)
    except (TypeError, ValueError):
        logger.warning("ignoring non-numeric memory_limit_mb=%r", memory_limit_mb)
        return MEMORY_LIMIT_BYTES
    if requested <= 0:
        logger.warning("ignoring non-positive memory_limit_mb=%s", requested)
        return MEMORY_LIMIT_BYTES
    clamped = min(max(requested, MEMORY_LIMIT_FLOOR_MB), MEMORY_LIMIT_CEIL_MB)
    if clamped != requested:
        logger.warning(
            "memory_limit_mb=%s clamped to %s MB", requested, clamped
        )
    return clamped * 1024 * 1024


def _memory_limit_mb_of(memory_limit_bytes: int) -> int:
    """Whole-MB figure to quote in a verdict (never ``0``, which reads as "none")."""
    return max(1, memory_limit_bytes // (1024 * 1024))


def _uid_task_count(uid: int) -> int:
    """Count the tasks (processes **and** threads) owned by ``uid``; -1 if unknown.

    ``RLIMIT_NPROC`` is charged against every task of the real uid - including
    the judge's own worker threads - so the limit has to be expressed as
    "whatever exists right now, plus a headroom". ``/proc`` is the only portable
    enough way to read that number on Linux; elsewhere the caller simply skips
    the limit instead of guessing - a guessed number either breaks ``fork()`` for
    valid submissions or protects nothing.
    """
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - non-Linux
        return -1
    total = 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status", "rb") as handle:
                blob = handle.read(4096)
        except OSError:
            continue  # vanished, or not ours to read
        # /proc lists thread-group leaders only, while the kernel charges every
        # task, so the per-process ``Threads:`` field is what makes the total real.
        ours = False
        threads = 0
        for line in blob.split(b"\n"):
            if line.startswith(b"Uid:"):
                fields = line.split()
                ours = len(fields) > 1 and fields[1].isdigit() and int(fields[1]) == uid
                if ours and threads:
                    break
            elif line.startswith(b"Threads:"):
                fields = line.split()
                threads = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 1
                if ours:
                    break
        if ours:
            total += threads or 1
    return total


def _nproc_soft_limit() -> Optional[int]:
    """Task ceiling for one submission, or ``None`` when it cannot be applied."""
    existing = _uid_task_count(os.getuid())
    if existing < 0:
        return None  # no /proc: refuse to guess a number that could break fork()
    hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
    target = existing + NPROC_HEADROOM
    if hard != resource.RLIM_INFINITY:
        target = min(target, hard)  # an unprivileged process may not raise the hard cap
    if target <= existing:
        # The kernel already allows no more than what exists right now, so the
        # limit would deny fork() to valid submissions. Leave it alone.
        return None
    return target


def _group_member_pids(pgid: int) -> List[int]:
    """Pids whose **current** process group is ``pgid`` (empty when unsupported).

    Used instead of ``os.killpg`` once the group leader has been reaped: a pgid
    is just a pid, and after the last member of a dead group is reaped the kernel
    may hand that number to an unrelated process. Naming the members explicitly
    can never signal a stranger.
    """
    pids: List[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - non-Linux
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                blob = handle.read(1024)
        except OSError:
            continue
        # ``comm`` may contain spaces and parentheses: the fixed fields start
        # after the last ')'. Fields there are state, ppid, pgrp, session, ...
        tail = blob.rsplit(b")", 1)[-1].split()
        if len(tail) > 2 and tail[2].isdigit() and int(tail[2]) == pgid:
            pids.append(pid)
    return pids


def _kill_process_group_members(pgid: int) -> int:
    """SIGKILL every live member of ``pgid``. Returns how many were signalled."""
    killed = 0
    for pid in _group_member_pids(pgid):
        try:
            os.kill(pid, _signal.SIGKILL)
            killed += 1
        except OSError:  # pragma: no cover - it exited a moment earlier
            pass
    if killed:
        logger.warning("killed %d leftover process(es) of group %s", killed, pgid)
    return killed


def _make_preexec(
    timeout_seconds: float,
    *,
    apply_memory_limit: bool,
    memory_limit_bytes: int = MEMORY_LIMIT_BYTES,
    nproc_soft: Optional[int] = None,
):
    """Return a ``preexec_fn`` that isolates the child in its own process group.

    The child is moved into a new session (``os.setsid``) so that the whole
    process group can be killed on timeout, which prevents runaway or zombie
    descendants. Resource limits are enforced by the kernel, so they still hold
    even if the submission bypasses our own timers with extra threads or
    processes. The working directory stays the per-run sandbox (set through
    ``Popen(cwd=...)``) so any file the submission writes is discarded.

    ``apply_memory_limit`` is False for the compiler itself: ``g++`` legitimately
    maps a large address space, so capping ``RLIMIT_AS`` there would break
    perfectly valid submissions. ``memory_limit_bytes`` is the ceiling for the
    submission - already resolved and clamped by the caller, because computing it
    here would mean allocating in a forked child of a threaded process.

    ``nproc_soft`` bounds task creation. Without it a submission such as
    ``for (int i = 0; i < 40; i++) fork();`` multiplies faster than the wall-clock
    kill can act and exhausts the host's task slots - the incident recorded in
    ``AGENTS.md``. Note that ``RLIMIT_CPU`` does *not* cover this: the CPU budget
    is per task, so every fork child starts with a fresh one.

    Nothing here may allocate or log: this runs in the child of a fork from a
    multi-threaded process, where only async-signal-safe work is survivable.
    Failures are folded into the return value of :func:`_execute` instead.
    """

    def _prepare() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()
        if apply_memory_limit:
            _setrlimit(resource.RLIMIT_AS, memory_limit_bytes)
        _setrlimit(resource.RLIMIT_CORE, 0)
        _setrlimit(resource.RLIMIT_FSIZE, FILE_SIZE_LIMIT_BYTES)
        _setrlimit(resource.RLIMIT_NOFILE, NOFILE_LIMIT)
        if nproc_soft is not None:
            _setrlimit(resource.RLIMIT_NPROC, nproc_soft)
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


def _drain_fd(fd: int, chunks: List[bytes], state: Dict[str, int], budget: Dict[str, float]) -> None:
    """Drain the raw pipe *fd* into ``chunks`` until EOF or until *budget* expires.

    Bytes are always consumed while the pipe produces them: dropping data without
    reading it would let a chatty program block on a full pipe and look like a
    hang. But draining must never wait for EOF indefinitely - a submission that
    forks and lets the parent exit while a descendant holds the pipe open would
    otherwise park this thread forever, along with the judge slot its caller
    holds. Hence the shared deadline, and hence why this thread - not the caller
    - owns the descriptor it closes.

    ``state`` carries ``kept``/``dropped`` bytes and raises ``stalled`` when the
    deadline expired with the pipe still open.
    """
    try:
        while True:
            if os.name == "posix":
                remaining = budget["deadline"] - time.monotonic()
                if remaining <= 0:
                    state["stalled"] = 1
                    break
                try:
                    ready = select.select([fd], [], [], min(remaining, 0.2))[0]
                except OSError:
                    break  # descriptor closed under us
                if not ready:
                    continue
            try:
                chunk = os.read(fd, 64 * 1024)
            except BlockingIOError:
                continue
            except OSError:
                break  # torn down by a kill
            if not chunk:
                break  # EOF
            if state["dropped"]:
                continue
            room = MAX_OUTPUT_BYTES - state["kept"]
            if room <= 0:
                state["dropped"] = 1
                continue
            if len(chunk) > room:
                state["dropped"] = 1
                chunk = chunk[:room]
            chunks.append(chunk)
            state["kept"] += len(chunk)
    finally:
        # This thread owns the descriptor: the caller must never close a pipe an
        # idle reader may still be sitting on.
        try:
            os.close(fd)
        except OSError:
            pass


def _feed_fd(fd: int, payload: bytes, budget: Dict[str, float], state: Dict[str, int]) -> None:
    """Write ``payload`` to a non-blocking stdin pipe, bounded by *budget*.

    A program that never reads its input used to pin the writer here, and the
    caller then blocked forever closing that same pipe. Chunks are gated on the
    pipe being writable, so the worst case is the deadline, not infinity.
    """
    try:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            if os.name == "posix":
                remaining = budget["deadline"] - time.monotonic()
                if remaining <= 0:
                    state["stalled"] = 1
                    break
                try:
                    ready = select.select([], [fd], [], min(remaining, 0.2))[1]
                except OSError:
                    break
                if not ready:
                    continue
            try:
                written = os.write(fd, view[offset : offset + 65536])
            except BlockingIOError:
                continue
            except OSError:
                break  # the program exited without reading its input
            if written <= 0:
                break
            offset += written
    finally:
        try:
            os.close(fd)
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
    #: True when a descendant outlived the direct child and still held a pipe.
    orphaned: bool = False


def _execute(
    argv: List[str],
    *,
    workdir: str,
    stdin_bytes: Optional[bytes],
    timeout_seconds: float,
    apply_memory_limit: bool,
    memory_limit_bytes: int = MEMORY_LIMIT_BYTES,
) -> _ProcOutcome:
    """Run ``argv`` under limits, feeding stdin and capturing capped output.

    The three pipes are created with :func:`os.pipe` and driven with
    :func:`select` on raw descriptors rather than through ``subprocess.PIPE``
    file objects. Two reasons, both about surviving hostile programs:

    * a buffered ``close()`` waits for a read in flight on the same object, so a
      single orphan holding a pipe open used to block the judge thread *forever*
      and leak one of the ``MAX_CONCURRENT_RUNS`` slots;
    * each descriptor has exactly one owner thread which closes it, so there is no
      close/read race and no chance of a recycled descriptor being drained by the
      thread that still believes it owns it.
    """
    env = _clean_env(workdir)
    # Task creation is capped for the *submission* only. The compiler is exempt:
    # gcc/g++ legitimately fork cc1, as and collect2, and a per-uid limit that is
    # momentarily too tight would surface as a bogus ``compile_error``.
    nproc_soft = _nproc_soft_limit() if apply_memory_limit else None
    child_fds: List[int] = []
    parent_fds: List[int] = []
    stdin_read: Optional[int] = None
    stdin_write: Optional[int] = None
    try:
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        parent_fds += [out_r, err_r]
        child_fds += [out_w, err_w]
        if stdin_bytes is not None:
            stdin_read, stdin_write = os.pipe()
            child_fds.append(stdin_read)
            parent_fds.append(stdin_write)
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            stdin=stdin_read if stdin_read is not None else subprocess.DEVNULL,
            stdout=out_w,
            stderr=err_w,
            env=env,
            close_fds=True,
            preexec_fn=_make_preexec(
                timeout_seconds,
                apply_memory_limit=apply_memory_limit,
                memory_limit_bytes=memory_limit_bytes,
                nproc_soft=nproc_soft,
            ),
        )
    except OSError as exc:
        for fd in set(child_fds + parent_fds):
            try:
                os.close(fd)
            except OSError:
                pass
        return _ProcOutcome(launch_error=f"failed to launch {argv[0]}: {exc}")

    # The child holds inherited copies, so the parent drops every descriptor it
    # does not drive itself: the two write ends (otherwise EOF never arrives) and
    # the stdin read end (otherwise a program could read someone else's input).
    for fd in child_fds:
        try:
            os.close(fd)
        except OSError:
            pass
    if stdin_write is not None:
        try:
            os.set_blocking(stdin_write, False)
        except OSError:  # pragma: no cover - pipe always supports this
            pass

    out_chunks: List[bytes] = []
    err_chunks: List[bytes] = []
    out_state = {"kept": 0, "dropped": 0, "stalled": 0}
    err_state = {"kept": 0, "dropped": 0, "stalled": 0}
    stdin_state = {"stalled": 0}

    start = time.perf_counter()
    deadline = time.monotonic() + timeout_seconds
    # One shared, shrinkable budget: the drains stop when the run is over, plus a
    # short grace for bytes already in flight.
    budget = {"deadline": deadline + PIPE_DRAIN_GRACE_SECONDS}

    readers = [
        threading.Thread(target=_drain_fd, args=(out_r, out_chunks, out_state, budget), daemon=True),
        threading.Thread(target=_drain_fd, args=(err_r, err_chunks, err_state, budget), daemon=True),
    ]
    for reader in readers:
        reader.start()

    stdin_thread: Optional[threading.Thread] = None
    if stdin_write is not None:
        stdin_thread = threading.Thread(
            target=_feed_fd,
            args=(stdin_write, stdin_bytes or b"", budget, stdin_state),
            daemon=True,
        )
        stdin_thread.start()

    timed_out = False
    exit_code: Optional[int] = None
    try:
        exit_code = proc.wait(timeout=max(deadline - time.monotonic(), 0.000001))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        try:
            exit_code = proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable is exotic on Linux
            logger.error("process group %s survived SIGKILL", proc.pid)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        # The direct child is gone (or was killed): the drains have the grace, no
        # more. This is what turns "an orphan holds the pipe" from a hang into a
        # one-second rounding error.
        budget["deadline"] = min(
            budget["deadline"], time.monotonic() + PIPE_DRAIN_GRACE_SECONDS
        )

    join_after = PIPE_DRAIN_GRACE_SECONDS + 0.5
    if stdin_thread is not None:
        stdin_thread.join(timeout=join_after)
    for reader in readers:
        reader.join(timeout=join_after)

    orphaned = bool(out_state["stalled"] or err_state["stalled"] or stdin_state["stalled"])
    if orphaned:
        # Somebody in our process group outlived the submission and still holds a
        # pipe. Name the members explicitly (never ``killpg`` a pgid whose leader
        # we already reaped) so they cannot linger and read the sandbox later.
        if _kill_process_group_members(proc.pid) == 0:
            logger.warning(
                "submission %s left a descendant that detached from process group "
                "%s; only a PID namespace can reclaim it", argv[0], proc.pid
            )
        for reader in readers:
            reader.join(timeout=PIPE_DRAIN_GRACE_SECONDS + 0.5)

    return _ProcOutcome(
        stdout=b"".join(out_chunks),
        stderr=b"".join(err_chunks),
        exit_code=exit_code,
        timed_out=timed_out,
        elapsed_ms=elapsed_ms,
        truncated=bool(out_state["dropped"] or err_state["dropped"]),
        orphaned=orphaned,
    )


def _kill_process_group(proc: "subprocess.Popen[bytes]") -> None:
    """SIGKILL the child's whole process group, then fall back to the child alone."""
    if proc.poll() is not None:
        # Reaped already, so ``killpg`` could hit a recycled pgid. The group can
        # still have members (that is exactly the fork-and-exit case), so kill the
        # members we can name instead of the group number.
        _kill_process_group_members(proc.pid)
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
    memory_limit_mb: Optional[int] = None,
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
        memory_limit_mb: address-space budget for **this** submission, i.e. the
            per-problem ``Problem.memory_limit_mb``. ``None`` keeps the global
            :data:`MEMORY_LIMIT_BYTES`; a given value is clamped to
            ``MEMORY_LIMIT_FLOOR_MB..MEMORY_LIMIT_CEIL_MB`` and is what the
            memory-limit verdict then quotes.

    Returns:
        A :class:`RunResult` describing status, captured output, exit code and
        measured execution time.
    """
    memory_limit_bytes = _resolve_memory_limit_bytes(memory_limit_mb)
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
            memory_limit_bytes=memory_limit_bytes,
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
        status, signal_name, notes = _classify(
            outcome, stderr_text, timeout_seconds, memory_limit_bytes
        )
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


def _internal_error_batch(spec_name: str, count: int, message: str) -> List["RunResult"]:
    """Build ``count`` independent ``internal_error`` results (never aliased)."""
    return [
        RunResult(status=Status.INTERNAL_ERROR, language=spec_name, stderr=message)
        for _ in range(count)
    ]


def run_code_batch(
    code: str,
    language: str,
    stdin_list: List[Optional[str]],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_mb: Optional[int] = None,
) -> List[RunResult]:
    """Compile ``code`` **once**, then run that binary once per stdin payload.

    This is the compile-once / run-many fast path of :func:`run_code`. Grading a
    submission against a problem means executing the *same* source against N test
    cases; recompiling for every case multiplies the dominant fixed cost by N
    (``N * (compile + run)`` -> ``compile + N * run``). One temporary directory
    holds the source and the binary, and each case executes in its own
    sub-directory, so files a program writes are confined to the sandbox and are
    dropped as soon as its case ends.

    The per-case directories are isolation *by convention*, not by force: the
    sandbox runs as the judge's own uid, so a submission can walk ``..`` and write
    in the parent - leaving a file for a later case, or making the shared binary
    writable again. Closing that properly means a dedicated uid, a read-only bind
    mount or a namespace (see ``AGENTS.md``), not a ``chmod``.

    Every returned :class:`RunResult` is aligned index-by-index with
    ``stdin_list``, so the caller can ``zip(cases, results)``. The result fields
    mean exactly what they mean in :func:`run_code`; ``compile_output`` and
    ``compile_time_ms`` are the single shared compile, copied onto each entry.

    Failure modes mirror :func:`run_code` and never raise:

    * unknown language / missing compiler -> one ``internal_error`` per case
      (``len(stdin_list)`` entries, so alignment always holds);
    * compile failure -> one ``compile_error`` per case carrying the compiler
      diagnostics, because no case could possibly have run;
    * a sandbox launch failure on one case -> ``internal_error`` for that case
      only, the remaining cases still execute.

    Args:
        code: the submission source text.
        language: ``"c"`` or ``"cpp"``.
        stdin_list: one stdin payload per execution (``None`` -> no stdin).
        timeout_seconds: wall-clock limit for **each** execution.
        memory_limit_mb: address-space budget for **each** execution, i.e. the
            per-problem ``Problem.memory_limit_mb``. ``None`` keeps the global
            :data:`MEMORY_LIMIT_BYTES`; see :func:`run_code`.

    Returns:
        ``len(stdin_list)`` results, in the order the payloads were given; an
        empty ``stdin_list`` returns an empty list without touching the toolchain.
    """
    count = len(stdin_list)
    if count == 0:
        return []

    # Resolved once for the whole batch: every case of one submission runs under
    # the same budget, and the verdict text has to quote the number that ran.
    memory_limit_bytes = _resolve_memory_limit_bytes(memory_limit_mb)

    try:
        spec = get_language_spec(language)
    except ValueError as exc:
        return _internal_error_batch(str(language), count, str(exc))

    compiler_path = shutil.which(spec.compiler)
    if compiler_path is None:
        return _internal_error_batch(
            spec.name, count, f"{spec.compiler} was not found on the server PATH"
        )

    workdir = tempfile.mkdtemp(prefix=f"cj-{spec.name}-", dir=_toolchain_sandbox())
    try:
        try:
            os.chmod(workdir, 0o700)
        except OSError:  # pragma: no cover
            pass

        source_path = os.path.join(workdir, spec.source_name)
        with open(source_path, "wb") as handle:
            handle.write((code or "").encode("utf-8", errors="replace"))
        try:
            os.chmod(source_path, 0o600)
        except OSError:  # pragma: no cover
            pass

        # -- one compile, shared by every case ------------------------------- #
        ok, compile_output, compile_rc, compile_ms, compile_trunc = _compile(
            workdir, spec
        )
        compile_time_ms = round(compile_ms, 3)
        if not ok:
            return [
                RunResult(
                    status=Status.COMPILE_ERROR,
                    compile_output=compile_output,
                    exit_code=compile_rc,
                    compile_time_ms=compile_time_ms,
                    execution_time_ms=0.0,
                    language=spec.name,
                    truncated=compile_trunc,
                )
                for _ in range(count)
            ]

        binary_path = os.path.join(workdir, spec.binary_name)
        try:
            # Read+execute only. This is *hardening*, not a boundary: the sandbox
            # shares the judge's uid (see AGENTS.md), and a file's owner can always
            # chmod their own file back, so a submission that really wants to
            # replace ``../solution`` between cases still can. What this does stop
            # is the accidental/self-inflicted overwrite of the binary the later
            # cases execute. Real enforcement needs a separate uid or a read-only
            # bind mount.
            os.chmod(binary_path, 0o500)
        except OSError:  # pragma: no cover
            pass

        results: List[RunResult] = []
        for idx, stdin_data in enumerate(stdin_list):
            try:
                # A private working directory per case, so anything the program
                # writes stays inside the sandbox and cannot confuse the next run.
                case_dir = os.path.join(workdir, f"run-{idx}")
                os.makedirs(case_dir, mode=0o700, exist_ok=False)
                try:
                    os.chmod(case_dir, 0o700)
                except OSError:  # pragma: no cover - umask only affects the mode
                    pass

                stdin_bytes = (
                    None if stdin_data is None else (stdin_data or "").encode("utf-8")
                )
                outcome = _execute(
                    [binary_path],
                    workdir=case_dir,
                    stdin_bytes=stdin_bytes,
                    timeout_seconds=timeout_seconds,
                    apply_memory_limit=True,
                    memory_limit_bytes=memory_limit_bytes,
                )

                if outcome.launch_error is not None:
                    results.append(
                        RunResult(
                            status=Status.INTERNAL_ERROR,
                            compile_output=compile_output,
                            stderr=outcome.launch_error,
                            compile_time_ms=compile_time_ms,
                            language=spec.name,
                        )
                    )
                    continue

                stdout_text = _decode(outcome.stdout, outcome.truncated, "stdout")
                stderr_text = _decode(outcome.stderr, outcome.truncated, "stderr")
                status, signal_name, notes = _classify(
                    outcome, stderr_text, timeout_seconds, memory_limit_bytes
                )
                if notes:
                    stderr_text = "\n".join(
                        part
                        for part in (stderr_text.rstrip("\n"), notes)
                        if part
                    )

                results.append(
                    RunResult(
                        status=status,
                        stdout=stdout_text,
                        stderr=stderr_text,
                        compile_output=compile_output,
                        exit_code=outcome.exit_code,
                        execution_time_ms=round(outcome.elapsed_ms, 3),
                        signal_name=signal_name,
                        compile_time_ms=compile_time_ms,
                        language=spec.name,
                        timed_out=outcome.timed_out
                        or status == Status.TIME_LIMIT_EXCEEDED,
                        truncated=outcome.truncated or compile_trunc,
                    )
                )
            except Exception as exc:  # defensive: one case must not void the rest
                logger.exception(
                    "unexpected failure in batch case %s of a %s submission", idx, spec.name
                )
                results.append(
                    RunResult(
                        status=Status.INTERNAL_ERROR,
                        compile_output=compile_output,
                        compile_time_ms=compile_time_ms,
                        language=spec.name,
                        stderr=(
                            "internal error while executing submission: "
                            f"{exc.__class__.__name__}: {exc}"
                        ),
                    )
                )
            finally:
                # Discard what this case wrote before running the next one. The
                # whole sandbox goes away at the end anyway, but ``RLIMIT_FSIZE``
                # caps a *file*, not the total: without this, a submission could
                # pile up case_dir after case_dir (50 x 256 files x 8 MB) and fill
                # the disk mid-batch, which would then fail every later submission
                # too.
                shutil.rmtree(case_dir, ignore_errors=True)
        return results
    except Exception as exc:  # defensive: never leak a 500 from user input
        logger.exception("unexpected failure while batching a %s submission", spec.name)
        return _internal_error_batch(
            spec.name,
            count,
            f"internal error while executing submission: {exc.__class__.__name__}: {exc}",
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _classify(
    outcome: _ProcOutcome,
    stderr_text: str,
    timeout_seconds: float,
    memory_limit_bytes: int = MEMORY_LIMIT_BYTES,
) -> Tuple[str, Optional[str], str]:
    """Map a finished process onto a judge status plus an explanatory note.

    ``memory_limit_bytes`` is the ceiling that was *actually* applied, so the note
    a student reads quotes the budget of the problem they submitted to rather than
    the server's global default.
    """
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
                f"{_memory_limit_mb_of(memory_limit_bytes)} MB exceeded]",
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
                f" - memory limit of {_memory_limit_mb_of(memory_limit_bytes)} MB "
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
        "nproc_headroom": NPROC_HEADROOM,
        "pipe_drain_grace_seconds": PIPE_DRAIN_GRACE_SECONDS,
        "languages": supported_languages(),
    }
