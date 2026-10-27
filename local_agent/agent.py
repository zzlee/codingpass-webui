#!/usr/bin/env python3
"""CodingPass Local Agent (學生端本地編譯執行代理).

A lightweight, zero-external-dependency WebSocket runner that runs on student PCs
(default `ws://127.0.0.1:8765`).

Offloads C/C++ compilation and playground / sample test runs completely to the
student's own machine, saving server CPU during 40+ student lectures.

Protocol:
---------
Client -> Agent:
  {"type": "ping"}
  {"type": "run", "id": "req-1", "code": "...", "language": "c"|"cpp", "stdin": "...", "timeout_sec": 3.0}
  {"type": "run_batch", "id": "req-2", "code": "...", "language": "c"|"cpp",
   "stdin_list": ["case-1", "case-2", ...], "timeout_sec": 3.0}

Agent -> Client:
  {"type": "pong", "version": "1.0.0", "compiler": "gcc 13.2.0"}
  {"type": "result", "id": "req-1", "status": "success"|"compile_error"|"time_limit_exceeded"|"runtime_error",
   "stdout": "...", "stderr": "...", "compile_output": "...", "execution_time_ms": 1.2, "exit_code": 0,
   "stdout_truncated": false, "stderr_truncated": false}
  {"type": "batch_result", "id": "req-2", "status": "ok"|"compile_error", "compile_time_ms": 42.1,
   "cases_executed": 2, "cases_skipped": 0, "results": [ {..same shape as result..}, ... ]}

Security notes:
-----------
* Browser clients must present an Origin that is loopback or a *private IP
  literal* (plus CODINGPASS_ALLOWED_ORIGINS); anything else gets HTTP 403
  (CSWSH). Prefix-style trust would be unsafe: only 172.16/12 is private, while
  e.g. 172.104.x.x is a public hosting range.
* Child processes run in their own session/process group, output is capped at
  MAX_OUTPUT_BYTES per stream, timeouts kill the whole process group, and every
  pipe drain is deadline-bounded so an orphan holding a pipe cannot wedge the
  agent. Compilation and each run happen inside the request's private temporary
  directory, never in the directory agent.py was started from.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

HOST = os.environ.get("CODINGPASS_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODINGPASS_AGENT_PORT", "8765"))
VERSION = "1.0.0"
WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ---------------------------------------------------------------------------
# Hardening limits
# ---------------------------------------------------------------------------

# Student programs are untrusted: never buffer more than this much stdout/stderr.
# Anything beyond the cap is still *drained* from the pipe (so the child can never
# deadlock blocked on a full pipe) but discarded, and reported via truncated flags.
MAX_OUTPUT_BYTES = 256 * 1024
COMPILE_TIMEOUT_SEC = 15.0
# A batch request may not ask for an unbounded number of runs. Keep it at or
# above the server's MAX_TEST_CASES (50), otherwise grading a full problem
# locally would silently drop the tail of the case list.
MAX_BATCH_CASES = max(1, int(os.environ.get("CODINGPASS_MAX_BATCH_CASES", "64")))
# Largest WebSocket text frame we will buffer. A client may declare a 64-bit
# length in a frame header, and reading it in would grow the buffer with whatever
# it decides to send, so an oversized frame ends the connection instead.
MAX_FRAME_BYTES = max(1024, int(os.environ.get("CODINGPASS_MAX_FRAME_BYTES", str(8 * 1024 * 1024))))
DEFAULT_TIMEOUT_SEC = 3.0
MAX_TIMEOUT_SEC = 30.0
# Grace given to stdout/stderr readers after the direct child has exited; beyond
# this any pipe still open belongs to an orphaned descendant, which we kill.
PIPE_CLOSE_GRACE_SEC = 1.0

# Cross-Site WebSocket Hijacking (CSWSH) defence: only same-LAN/browser origins
# used by the CodingPass WebUI are allowed to complete the WebSocket handshake.
# There is no shared secret, so this check is the only thing between a web page
# and "compile and run anything on this PC": trust is limited to loopback and to
# genuinely private address literals (see :func:`_private_lan_host`), plus an
# explicit operator allow-list.
ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_ORIGINS_ENV = "CODINGPASS_ALLOWED_ORIGINS"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("codingpass-agent")


def find_compiler(lang: str) -> Optional[str]:
    """Find gcc/g++ (MinGW on Windows) or clang/clang++ in PATH or standard installation directories."""
    candidates = (
        ["gcc", "clang", "x86_64-w64-mingw32-gcc"]
        if lang == "c"
        else ["g++", "clang++", "x86_64-w64-mingw32-g++"]
    )

    # 1. Check relative bundle (w64devkit / mingw64) in the same directory as agent.py
    base_dir = Path(__file__).resolve().parent
    for sub in ["w64devkit/bin", "mingw64/bin", "mingw/bin", "bin"]:
        bundle_bin = base_dir / sub
        if bundle_bin.is_dir():
            for name in candidates:
                exe = bundle_bin / (f"{name}.exe" if sys.platform == "win32" else name)
                if exe.is_file():
                    return str(exe)

    # 2. Check PATH
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found

    # 3. On Windows, proactively search common MinGW / MSYS2 / CodeBlocks / Dev-Cpp locations
    if sys.platform == "win32":
        common_dirs = [
            Path(r"C:\msys64\mingw64\bin"),
            Path(r"C:\msys64\ucrt64\bin"),
            Path(r"C:\MinGW\bin"),
            Path(r"C:\Program Files\CodeBlocks\MinGW\bin"),
            Path(r"C:\Program Files (x86)\CodeBlocks\MinGW\bin"),
            Path(r"C:\Program Files (x86)\Dev-Cpp\MinGW64\bin"),
            Path(r"C:\w64devkit\bin"),
            Path(r"C:\winlibs\bin"),
        ]
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            common_dirs.append(Path(user_profile) / r"scoop\apps\gcc\current\bin")

        for d in common_dirs:
            if d.is_dir():
                for name in candidates:
                    exe = d / f"{name}.exe"
                    if exe.is_file():
                        return str(exe)

    return None


_COMPILER_INFO_CACHE: Optional[str] = None


def get_compiler_info() -> str:
    """Return compiler description for ping response (cached, may block ~2s once).

    The description is probed by *running* the compiler, so this is not safe to
    call straight from the asyncio event loop more than once: callers that could
    repeat (the WebSocket ``ping`` handler) go through an executor.
    """
    global _COMPILER_INFO_CACHE
    if _COMPILER_INFO_CACHE is not None:
        return _COMPILER_INFO_CACHE
    c_comp = find_compiler("c")
    cpp_comp = find_compiler("cpp")
    if not cpp_comp and not c_comp:
        _COMPILER_INFO_CACHE = "No C/C++ compiler found (install GCC or MinGW)"
        return _COMPILER_INFO_CACHE
    target = cpp_comp or c_comp
    try:
        proc = subprocess.run(
            [target, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
        )
        line = proc.stdout.splitlines()[0] if proc.stdout else target
    except Exception:
        line = target
    _COMPILER_INFO_CACHE = line
    return _COMPILER_INFO_CACHE


# ---------------------------------------------------------------------------
# Sandbox-lite execution primitives
# ---------------------------------------------------------------------------


class _RunResult(NamedTuple):
    """Bounded result of running one local process."""

    stdout: bytes
    stderr: bytes
    returncode: int
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class _OutputBuffer:
    """Accumulate at most `cap` bytes while always draining the pipe."""

    __slots__ = ("data", "truncated")

    def __init__(self) -> None:
        self.data = bytearray()
        self.truncated = False

    def feed(self, chunk: bytes, cap: int) -> None:
        if not chunk:
            return
        room = cap - len(self.data)
        if room >= len(chunk):
            self.data.extend(chunk)
            return
        if room > 0:
            self.data.extend(chunk[:room])
        self.truncated = True


def _pump_stream(
    stream: Any,
    buf: _OutputBuffer,
    cap: int,
    budget: Dict[str, float],
    stall: Optional[Dict[str, bool]] = None,
    stall_key: str = "",
) -> None:
    """Drain `stream` into `buf` until EOF **or until the shared budget expires**.

    Draining to EOF is not enough on its own: a submission that forks and lets the
    parent exit while a descendant keeps the pipe open would park this thread - and
    whatever waits for it or closes that stream - forever, because a buffered
    ``close()`` waits for a read in flight. So the pump is deadline-bounded, and so
    that no two owners ever touch the same descriptor, this thread (not the caller)
    closes the stream it drains.
    """
    try:
        read1 = getattr(stream, "read1", None)
        while True:
            if os.name == "posix":
                remaining = budget["deadline"] - time.monotonic()
                if remaining <= 0:
                    if stall is not None:
                        stall[stall_key] = True  # somebody else still holds the pipe
                    break
                try:
                    ready = select.select([stream], [], [], min(remaining, 0.2))[0]
                except (OSError, ValueError):
                    break  # descriptor closed under us
                if not ready:
                    continue
            # read1() returns as soon as *any* data is available, which keeps the
            # pipe draining promptly even after we stopped storing bytes.
            try:
                chunk = read1(65536) if read1 is not None else stream.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf.feed(chunk, cap)
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _write_stdin(
    stream: Any,
    payload: bytes,
    budget: Dict[str, float],
    stall: Optional[Dict[str, bool]] = None,
) -> None:
    """Feed stdin from a thread, gated on writability and on the shared budget.

    A program that never reads its input must not be able to pin this thread (and
    therefore the caller's ``close()``) indefinitely, so writes are chunked and
    only issued when the pipe accepts them.
    """
    try:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            if os.name == "posix":
                remaining = budget["deadline"] - time.monotonic()
                if remaining <= 0:
                    if stall is not None:
                        stall["in"] = True
                    break
                try:
                    ready = select.select([], [stream], [], min(remaining, 0.2))[1]
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
            try:
                written = stream.write(view[offset : offset + 65536]) or 65536
                stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                break  # child exited early (or never read stdin) - not an error
            offset += written
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _group_member_pids(pgid: int) -> List[int]:
    """Pids whose **current** process group is ``pgid`` (POSIX, ``/proc`` only).

    Once the group leader has been reaped, ``os.killpg(pgid)`` is no longer safe:
    a pgid is just a pid, and the kernel may hand that number to an unrelated
    process. Naming the live members can never signal a stranger.
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
        # ``comm`` can hold spaces and parentheses: fixed fields start after the
        # last ')' and are state, ppid, pgrp, session, ...
        tail = blob.rsplit(b")", 1)[-1].split()
        if len(tail) > 2 and tail[2].isdigit() and int(tail[2]) == pgid:
            pids.append(pid)
    return pids


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the child *and every descendant* it spawned (fork-bomb defence)."""
    killed = False
    if os.name == "posix":
        # start_new_session=True makes the child its own session/group leader,
        # so pgid == pid and killpg can only ever target our own process tree
        # (never, say, the agent's own group).
        if proc.poll() is not None:
            # Already reaped (the orphan-cleanup path): kill by name instead of by
            # group number, because that number can already belong to someone else.
            members = _group_member_pids(proc.pid)
            for pid in members:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            killed = bool(members)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                killed = True
            except (ProcessLookupError, PermissionError, OSError):
                killed = False
    else:
        try:
            finished = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            # taskkill exits non-zero when it could not kill anything (access
            # denied, unknown pid); treating that as success left orphans running.
            killed = finished.returncode == 0
        except Exception:  # noqa: BLE001 - best-effort fallback below
            killed = False

    if not killed:
        try:
            proc.kill()
        except OSError:
            pass


def _run_bounded(
    cmd: List[str],
    stdin_data: bytes = b"",
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    cap: int = MAX_OUTPUT_BYTES,
    cwd: Optional[str] = None,
) -> _RunResult:
    """Run `cmd` in its own session/process group with bounded output capture.

    Guarantees:
      * stdout/stderr are drained concurrently (no pipe deadlock), and every drain
        is bounded by a shared deadline, so an orphan that keeps a pipe open can
        never block the caller;
      * at most `cap` bytes per stream are retained, the rest is discarded and
        flagged via the ``*_truncated`` fields;
      * on wall-clock timeout the whole process group is SIGKILLed (POSIX) or
        tree-killed with ``taskkill /T`` (Windows), so no orphan survives;
      * ``cwd`` defaults to the *agent's* directory only when the caller says
        nothing - every real call site passes the run's private temporary
        directory, so a submission cannot litter (or read) the student's real
        working directory.
    """
    popen_kwargs: Dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if cwd:
        popen_kwargs["cwd"] = cwd
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603 - fixed argv, no shell
    except OSError as exc:
        return _RunResult(
            stdout=b"",
            stderr=f"Failed to launch '{cmd[0]}': {exc}".encode("utf-8"),
            returncode=-1,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    out_buf = _OutputBuffer()
    err_buf = _OutputBuffer()
    # One shrinkable budget shared by all three pipe workers, plus the flags they
    # raise when they gave up on a pipe that never reached EOF - the signal that a
    # descendant outlived the direct child.
    budget = {"deadline": time.monotonic() + timeout_sec + PIPE_CLOSE_GRACE_SEC}
    stall: Dict[str, bool] = {"out": False, "err": False, "in": False}
    workers = [
        threading.Thread(
            target=_pump_stream, args=(proc.stdout, out_buf, cap, budget, stall, "out"), daemon=True
        ),
        threading.Thread(
            target=_pump_stream, args=(proc.stderr, err_buf, cap, budget, stall, "err"), daemon=True
        ),
        threading.Thread(
            target=_write_stdin, args=(proc.stdin, stdin_data, budget, stall), daemon=True
        ),
    ]
    for worker in workers:
        worker.start()

    timed_out = False
    try:
        try:
            returncode = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(proc)
            try:
                returncode = proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                # Belt and braces: something in the tree still refused to die.
                _kill_process_tree(proc)
                try:
                    returncode = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    returncode = -9
    finally:
        # The direct child is gone (or was killed): the pipe workers now have at
        # most the grace period left, whoever they were draining for.
        budget["deadline"] = min(
            budget["deadline"], time.monotonic() + PIPE_CLOSE_GRACE_SEC
        )
        if not timed_out:
            # The direct child is gone; if a drain gave up instead of seeing EOF an
            # orphan inherited the pipes, so take the (still existing) group down.
            for reader_worker in workers[:2]:
                reader_worker.join(
                    timeout=max(0.0, budget["deadline"] - time.monotonic())
                )
        if stall["out"] or stall["err"] or stall["in"] or any(
            worker.is_alive() for worker in workers[:2]
        ):
            # Works for the timeout path too: everything in the group is meant to
            # die with the submission.
            _kill_process_tree(proc)
        for worker in workers:
            worker.join(timeout=PIPE_CLOSE_GRACE_SEC + 2.0)
        # The workers own the three streams and close them themselves: closing a
        # buffered stream from here while one of them still has a read in flight
        # is what used to block this thread forever.
        # Reap defensively in case the readers outlived their join timeout.
        try:
            proc.poll()
        except OSError:
            pass

    return _RunResult(
        stdout=bytes(out_buf.data),
        stderr=bytes(err_buf.data),
        returncode=returncode,
        timed_out=timed_out,
        stdout_truncated=out_buf.truncated,
        stderr_truncated=err_buf.truncated,
    )


def _decode(raw: bytes, truncated: bool, stream_name: str) -> str:
    """Decode captured bytes and append a visible truncation banner if needed."""
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        notice = (
            f"\n[codingpass-agent] {stream_name} exceeded "
            f"{MAX_OUTPUT_BYTES} bytes and was truncated.\n"
        )
        text = text + notice
    return text


def _binary_name() -> str:
    return "solution.exe" if sys.platform == "win32" else "solution"


def _compiler_missing_payload(language: str) -> Dict[str, object]:
    """Uniform compile_error payload when no toolchain is installed."""
    needed = "gcc" if language == "c" else "g++"
    return {
        "status": "compile_error",
        "stdout": "",
        "stderr": f"Local compiler not found for language '{language}'.",
        "compile_output": (
            f"Error: Neither '{needed}' was found in PATH.\n"
            "Please install GCC/Clang or extract w64devkit beside agent.py."
        ),
        "exit_code": 1,
        "compile_time_ms": 0.0,
        "execution_time_ms": 0.0,
        "timed_out": False,
    }


def _compile_source(
    compiler_path: str,
    code: str,
    language: str,
    tmp_dir: str,
) -> Dict[str, object]:
    """Compile `code` into tmp_dir. Never raises; returns an ok/bin/error record."""
    ext = "c" if language == "c" else "cpp"
    src_path = os.path.join(tmp_dir, f"source.{ext}")
    bin_path = os.path.join(tmp_dir, _binary_name())

    try:
        with open(src_path, "w", encoding="utf-8") as src_file:
            src_file.write(code)
    except OSError as exc:
        return {
            "ok": False,
            "bin_path": "",
            "output": f"Could not write temporary source file: {exc}",
            "time_ms": 0.0,
            "returncode": 1,
            "timed_out": False,
        }

    compile_cmd = [compiler_path, "-O2", src_path, "-o", bin_path]
    if language == "cpp":
        compile_cmd.append("-std=c++17")

    c_t0 = time.perf_counter()
    # cwd=tmp_dir: gcc (and anything a source can make it run) must not work in the
    # directory the agent was started from, which is usually the student's own.
    compiled = _run_bounded(compile_cmd, b"", COMPILE_TIMEOUT_SEC, cwd=tmp_dir)
    compile_time_ms = round((time.perf_counter() - c_t0) * 1000.0, 2)

    if compiled.timed_out:
        return {
            "ok": False,
            "bin_path": "",
            "output": f"Compilation timed out after {COMPILE_TIMEOUT_SEC:.0f}s.",
            "time_ms": compile_time_ms,
            "returncode": 1,
            "timed_out": True,
        }

    compile_output = (
        _decode(compiled.stdout, compiled.stdout_truncated, "compile stdout")
        + "\n"
        + _decode(compiled.stderr, compiled.stderr_truncated, "compile stderr")
    ).strip()

    produced_binary = os.path.isfile(bin_path)
    if produced_binary:
        try:
            # Read+execute only: a program that reopens its own argv[0] must not be
            # able to overwrite the binary the *later* cases of a batch execute.
            os.chmod(bin_path, 0o500)
        except OSError:  # pragma: no cover - exotic filesystems
            pass
    elif compiled.returncode == 0:
        compile_output = (
            compile_output + "\n[codingpass-agent] the compiler exited 0 but produced "
            "no executable; treating this as a compilation failure."
        ).strip()

    return {
        # A compiler that exits 0 without producing the binary is a failure, not a
        # "runtime_error" from the launch attempt that follows.
        "ok": compiled.returncode == 0 and produced_binary,
        "bin_path": bin_path,
        "output": compile_output,
        "time_ms": compile_time_ms,
        "returncode": compiled.returncode,
        "timed_out": False,
    }


def _run_binary(
    bin_path: str, stdin_data: str, timeout_sec: float, cwd: Optional[str] = None
) -> Dict[str, object]:
    """Execute an already compiled binary once and map it to a judge result dict."""
    e_t0 = time.perf_counter()
    run = _run_bounded(
        [bin_path], stdin_data.encode("utf-8", errors="replace"), timeout_sec, cwd=cwd
    )
    exec_time_ms = round((time.perf_counter() - e_t0) * 1000.0, 2)

    if run.timed_out:
        status = "time_limit_exceeded"
        stderr_text = (
            f"Execution exceeded wall-clock limit of {timeout_sec:.1f}s "
            "(process group killed)."
        )
        exit_code = -9
    else:
        exit_code = run.returncode
        status = "success" if exit_code == 0 else "runtime_error"
        stderr_text = _decode(run.stderr, run.stderr_truncated, "stderr")

    return {
        "status": status,
        "stdout": _decode(run.stdout, run.stdout_truncated, "stdout"),
        "stderr": stderr_text,
        "exit_code": exit_code,
        "execution_time_ms": exec_time_ms,
        "timed_out": run.timed_out,
        "stdout_truncated": run.stdout_truncated,
        "stderr_truncated": run.stderr_truncated,
        "truncated": run.stdout_truncated or run.stderr_truncated,
    }


def execute_local_code(
    code: str,
    language: str,
    stdin_data: str = "",
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Dict[str, object]:
    """Compile and execute source code locally in a temporary directory."""
    language = language if language in ("c", "cpp") else "cpp"
    compiler_path = find_compiler(language)
    if not compiler_path:
        return _compiler_missing_payload(language)

    with tempfile.TemporaryDirectory(prefix="cp-agent-") as tmp_dir:
        compiled = _compile_source(compiler_path, code, language, tmp_dir)
        if not compiled["ok"]:
            return {
                "status": "compile_error",
                "stdout": "",
                "stderr": "",
                "compile_output": compiled["output"],
                "exit_code": compiled["returncode"],
                "compile_time_ms": compiled["time_ms"],
                "execution_time_ms": 0.0,
                "timed_out": compiled["timed_out"],
            }

        # The submission runs *inside* its private temporary directory, so any file
        # it creates is discarded with it instead of landing in whatever directory
        # the student started agent.py from.
        run_dir = os.path.join(tmp_dir, "run-0")
        os.makedirs(run_dir, exist_ok=True)
        result = _run_binary(compiled["bin_path"], stdin_data, timeout_sec, cwd=run_dir)
        result["compile_output"] = compiled["output"]
        result["compile_time_ms"] = compiled["time_ms"]
        return result


def execute_local_batch(
    code: str,
    language: str,
    stdin_list: List[str],
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Dict[str, object]:
    """Compile ONCE, then run the binary once per stdin case (judging a test set).

    Each case executes in its own sub-directory of the run's temporary directory,
    exactly like the server-side sandbox, so files one case writes cannot be read
    back by the next one.
    """
    language = language if language in ("c", "cpp") else "cpp"
    compiler_path = find_compiler(language)
    if not compiler_path:
        missing = _compiler_missing_payload(language)
        missing.update(
            {
                "results": [],
                "cases_executed": 0,
                "cases_skipped": len(stdin_list or []),
            }
        )
        return missing

    requested = len(stdin_list or [])
    cases = [case if isinstance(case, str) else str(case) for case in (stdin_list or [])]
    # Report the *dropped* cases; on a compile failure every requested case is
    # skipped, not just the ones that survived the cap.
    skipped = max(0, requested - MAX_BATCH_CASES)
    cases = cases[:MAX_BATCH_CASES]

    with tempfile.TemporaryDirectory(prefix="cp-agent-") as tmp_dir:
        compiled = _compile_source(compiler_path, code, language, tmp_dir)
        if not compiled["ok"]:
            return {
                "status": "compile_error",
                "compile_output": compiled["output"],
                "compile_time_ms": compiled["time_ms"],
                "cases_executed": 0,
                "cases_skipped": skipped + len(cases),
                "results": [],
            }

        results: List[Dict[str, object]] = []
        for index, case_stdin in enumerate(cases):
            case_dir = os.path.join(tmp_dir, f"run-{index}")
            try:
                os.makedirs(case_dir, exist_ok=True)
            except OSError as exc:  # pragma: no cover - disk full, permissions
                results.append(
                    {
                        "index": index,
                        "status": "internal_error",
                        "stdout": "",
                        "stderr": f"Could not create the run directory: {exc}",
                        "exit_code": -1,
                        "execution_time_ms": 0.0,
                        "timed_out": False,
                    }
                )
                continue
            case_result = _run_binary(
                compiled["bin_path"], case_stdin, timeout_sec, cwd=case_dir
            )
            case_result["index"] = index
            results.append(case_result)

        return {
            "status": "ok",
            "compile_output": compiled["output"],
            "compile_time_ms": compiled["time_ms"],
            "cases_executed": len(results),
            "cases_skipped": skipped,
            "results": results,
        }


def _coerce_timeout(raw: Any) -> float:
    """Clamp a client supplied timeout into a sane range (never unbounded)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    if value <= 0:
        return DEFAULT_TIMEOUT_SEC
    return min(value, MAX_TIMEOUT_SEC)


def _normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _origin_parts(origin: str) -> Tuple[str, str]:
    """Return ``(scheme, host)`` for an Origin / allow-list entry.

    A bare ``host[:port]`` entry yields an empty scheme, which means "any scheme".
    Ports are ignored, IPv6 brackets are stripped, '' marks an unparsable value.
    """
    candidate = origin.strip().rstrip("/")
    has_scheme = "://" in candidate
    if not has_scheme:
        candidate = "http://" + candidate
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return "", ""
    host = (parsed.hostname or "").strip("[]").lower()
    scheme = parsed.scheme.lower() if has_scheme else ""
    return scheme, host


def _env_allowed_origins() -> List[str]:
    raw = os.environ.get(ALLOWED_ORIGINS_ENV, "") or ""
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def _private_lan_host(host: str) -> bool:
    """True for addresses inside the private ranges used by classroom LANs.

    Two rules matter:

    * the value must *be* an IP literal. Checking a hostname against prefixes
      would trust ``10.attacker.example.com``, and trusting the whole ``172.``
      prefix would trust public space too - only ``172.16.0.0/12`` is private,
      while e.g. ``172.104.x.x`` (a hosting provider) is routable from anywhere.
      A browser page served from such a host would otherwise pass this check and
      gain arbitrary local code execution through the agent (CSWSH).
    * IPv6 mapped forms are resolved before deciding, so ``::ffff:8.8.8.8`` is
      judged by the address it really is.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # ``ipv4_mapped`` exists on IPv6Address only.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    # Link-local and unique-local are fine for a classroom LAN; "private" already
    # covers 10/8, 172.16/12, 192.168/16, fc00::/7 and fe80::/10.
    return bool(address.is_private)


def is_allowed_origin(origin: Optional[str]) -> bool:
    """Allow loopback / private-address origins plus an explicit env allow-list.

    A missing Origin header is accepted so that CLI tooling, automated tests and
    other non-browser clients can still talk to the agent; every browser request
    (including cross-origin ones) *must* send an Origin header that passes here.
    That trade-off is only safe while the agent listens on loopback - see
    :func:`main`, which refuses to treat a non-loopback bind as private.
    """
    if origin is None or not origin.strip():
        return True

    normalized = _normalize_origin(origin)
    req_scheme, req_host = _origin_parts(normalized)

    # 1. Explicit operator allow-list (CODINGPASS_ALLOWED_ORIGINS="http://a,http://b").
    #    Scheme-less entries ("pc.lan") match any scheme; ports are always ignored.
    for entry in _env_allowed_origins():
        entry_scheme, entry_host = _origin_parts(entry)
        if not entry_host or entry_host != req_host:
            continue
        if not entry_scheme or entry_scheme == req_scheme:
            return True

    # 2. Loopback.
    if req_host in ALLOWED_ORIGIN_HOSTS:
        return True

    # 3. Private LAN ranges used by classroom servers - IP literals only, and only
    #    the ranges that are actually private (172.16/12, not the whole 172/8).
    return _private_lan_host(req_host)


class MinimalWebSocket:
    """RFC 6455 WebSocket over asyncio reader/writer."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def handshake(self) -> bool:
        """Read HTTP upgrade request and send 101 Switching Protocols."""
        request_line = await self.reader.readline()
        if not request_line:
            return False

        headers: Dict[str, str] = {}
        while True:
            line = await self.reader.readline()
            if not line or line == b"\r\n":
                break
            parts = line.decode("latin1", errors="ignore").split(":", 1)
            if len(parts) == 2:
                headers[parts[0].strip().lower()] = parts[1].strip()

        ws_key = headers.get("sec-websocket-key")
        if not ws_key:
            return False

        # CSWSH defence: browsers always send Origin on a WebSocket upgrade, so
        # reject any upgrade that comes from an untrusted site. A missing Origin
        # (curl / python test clients / CLI tooling) is allowed through.
        origin = headers.get("origin")
        if not is_allowed_origin(origin):
            logger.warning("Rejected WebSocket upgrade from untrusted Origin: %r", origin)
            self.writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await self.writer.drain()
            return False

        # Compute accept hash
        accept_raw = hashlib.sha1((ws_key + WS_MAGIC_GUID).encode("utf-8")).digest()
        accept_val = base64.b64encode(accept_raw).decode("utf-8")

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_val}\r\n"
            "\r\n"
        )
        self.writer.write(response.encode("utf-8"))
        await self.writer.drain()
        return True

    async def recv_text(self) -> Optional[str]:
        """Read next WebSocket text frame.

        Returns the payload, or ``None`` to mean "close this connection". Frame
        lengths are capped: a client may declare a 64-bit length in the header,
        and honouring it would let it grow our receive buffer without bound.
        Ping frames are answered in a loop rather than by recursing, so a flood of
        pings cannot walk the stack into a ``RecursionError``.
        """
        while True:
            try:
                head = await self.reader.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                return None

            b1, b2 = head[0], head[1]
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F

            if opcode == 0x8:  # Close frame
                return None
            if opcode == 0x9:  # Ping
                await self.send_pong()
                continue
            if opcode == 0xA:  # Pong: keepalive echo, not a message
                continue

            try:
                if length == 126:
                    ext = await self.reader.readexactly(2)
                    length = struct.unpack("!H", ext)[0]
                elif length == 127:
                    ext = await self.reader.readexactly(8)
                    length = struct.unpack("!Q", ext)[0]
            except (asyncio.IncompleteReadError, ConnectionResetError, struct.error):
                return None

            if length > MAX_FRAME_BYTES:
                logger.warning(
                    "Closing connection: declared frame size %d exceeds %d bytes",
                    length,
                    MAX_FRAME_BYTES,
                )
                return None

            mask = await self.reader.readexactly(4) if masked else None
            payload = await self.reader.readexactly(length)

            if masked and mask:
                payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == 0x1:  # Text frame
                return payload.decode("utf-8", errors="replace")
            # Binary, continuation or unknown opcodes are not part of this protocol.
            return None

    async def send_text(self, text: str) -> None:
        """Send a WebSocket text frame."""
        if self.closed:
            return
        payload = text.encode("utf-8")
        length = len(payload)

        header = bytearray([0x81])  # FIN + Text opcode
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))

        try:
            self.writer.write(header + payload)
            await self.writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self.closed = True

    async def send_pong(self) -> None:
        """Send pong frame."""
        try:
            self.writer.write(bytearray([0x8A, 0x00]))
            await self.writer.drain()
        except Exception:
            pass

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.writer.write(bytearray([0x88, 0x00]))
                await self.writer.drain()
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    ws = MinimalWebSocket(reader, writer)
    peer = writer.get_extra_info("peername")
    logger.info("New connection from %s", peer)

    if not await ws.handshake():
        logger.warning("Handshake failed for %s", peer)
        writer.close()
        return

    logger.info("WebSocket handshake successful for %s", peer)

    try:
        while True:
            msg = await ws.recv_text()
            if msg is None:
                break

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            if not isinstance(data, dict):
                await ws.send_text(
                    json.dumps({"type": "error", "message": "Payload must be a JSON object"})
                )
                continue

            msg_type = data.get("type")

            if msg_type == "ping":
                # get_compiler_info() *runs* the compiler (up to 2s). Off the loop,
                # or one client pinging in a series would stall every connection.
                loop = asyncio.get_running_loop()
                compiler_info = await loop.run_in_executor(None, get_compiler_info)
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "pong",
                            "version": VERSION,
                            "compiler": compiler_info,
                        }
                    )
                )

            elif msg_type == "run":
                req_id = data.get("id")
                code = str(data.get("code", ""))
                language = str(data.get("language", "cpp"))
                stdin_data = str(data.get("stdin", ""))
                timeout_sec = _coerce_timeout(data.get("timeout_sec", DEFAULT_TIMEOUT_SEC))

                logger.info(
                    "Executing %s code (req_id=%s, len=%d chars)",
                    language,
                    req_id,
                    len(code),
                )

                # Run compilation & execution in thread pool to not block asyncio event loop
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, execute_local_code, code, language, stdin_data, timeout_sec
                )

                reply = {
                    "type": "result",
                    "id": req_id,
                    **result,
                }
                await ws.send_text(json.dumps(reply))

            elif msg_type == "run_batch":
                req_id = data.get("id")
                code = str(data.get("code", ""))
                language = str(data.get("language", "cpp"))
                timeout_sec = _coerce_timeout(data.get("timeout_sec", DEFAULT_TIMEOUT_SEC))

                raw_cases = data.get("stdin_list", [])
                if not isinstance(raw_cases, list):
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "id": req_id,
                                "message": "stdin_list must be an array of strings",
                            }
                        )
                    )
                    continue

                logger.info(
                    "Executing %s code in batch (req_id=%s, cases=%d, len=%d chars)",
                    language,
                    req_id,
                    len(raw_cases),
                    len(code),
                )

                loop = asyncio.get_running_loop()
                batch_res = await loop.run_in_executor(
                    None, execute_local_batch, code, language, raw_cases, timeout_sec
                )

                await ws.send_text(
                    json.dumps({"type": "batch_result", "id": req_id, **batch_res})
                )

            else:
                await ws.send_text(
                    json.dumps({"type": "error", "message": f"Unknown type: {msg_type}"})
                )

    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001 - a broken client must never kill the agent
        logger.exception("Unhandled error while serving %s", peer)
    finally:
        logger.info("Connection closed for %s", peer)
        await ws.close()


def _bind_is_loopback(host: str) -> bool:
    """True when ``CODINGPASS_AGENT_HOST`` only accepts connections from this PC."""
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def main() -> None:
    logger.info("==================================================")
    logger.info("  CodingPass Local Agent (學生端本地編譯執行服務)")
    logger.info("  Version: %s", VERSION)
    logger.info("  Detected Compiler: %s", get_compiler_info())
    logger.info("  Listening on ws://%s:%d", HOST, PORT)
    logger.info(
        "  Extra allowed origins: %s",
        os.environ.get(ALLOWED_ORIGINS_ENV, "") or "(none, loopback + private LAN only)",
    )
    if not _bind_is_loopback(HOST):
        # There is no shared secret: the Origin header is a *browser* control, and
        # a non-browser client simply omits it. On a routable interface anyone who
        # can reach this port can compile and run anything on this PC.
        logger.warning(
            "BINDING TO %s IS NOT LOOPBACK. The agent compiles and runs whatever it "
            "is sent and authenticates browsers only, so every machine that can "
            "reach this port can run code here. Keep CODINGPASS_AGENT_HOST=127.0.0.1 "
            "unless the network is fully trusted.",
            HOST,
        )
    logger.info("==================================================")

    server = await asyncio.start_server(handle_client, HOST, PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("CodingPass Local Agent stopped.")
