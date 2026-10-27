#!/usr/bin/env python3
"""CodingPass Exam Lockdown Module (學生端考試鎖定模組).

Self-contained, zero-third-party-dependency Windows exam lockdown built on the
raw Win32 API through Python's built-in ``ctypes``:

* **Low-Level Keyboard Hook** – ``SetWindowsHookExW(WH_KEYBOARD_LL, ...)`` runs
  on a dedicated message-pump thread and swallows exam-disrupting key
  combinations before they ever reach another application:

  - Left / Right Windows key (``VK_LWIN`` 0x5B / ``VK_RWIN`` 0x5C).  Swallowing
    the Win *key-down* also neutralises every Win shortcut (Win+D, Win+E,
    Win+R, ...) because the shell never sees the Win key press.
  - Alt+Tab / Alt+Shift+Tab (task switching)
  - Alt+Esc (z-order cycling)
  - Ctrl+Esc and Ctrl+Shift+Esc (Start menu / Task Manager)
  - Alt+F4 (close the exam window)

* **Safety precautions** – the lockdown can never accidentally outlive the
  agent process:

  - ``atexit`` hook unhooks every live instance at interpreter shutdown.
  - SIGINT / SIGTERM handlers release the lock before chained handling.
  - Heartbeat watchdog: if the caller stops beating (exam app crashed / lost
    network), the lock auto-releases after ``heartbeat_timeout_sec`` so the
    student machine is not bricked by a dead supervisor.
  - The pump thread unhooks in ``finally`` even if it exits abnormally.

* **Graceful non-Windows fallback** – on Linux/macOS a fully functional
  *simulated* hook is used instead.  The blocked-combination logic is shared
  verbatim with the real Windows path (``key_combination_blocked``), so unit
  tests written against the simulator genuinely exercise production logic.

* ``launch_edge_kiosk(url)`` spawns Microsoft Edge in fullscreen kiosk mode.

Not achievable with a low-level keyboard hook (documented limitations):
Ctrl+Alt+Del is a Secure Attention Sequence handled by the kernel and cannot
be intercepted in user space; production deployments should additionally use
Group Policy / Kiosk policies for that.

Usage (Windows)::

    from exam_lock import ExamLock

    lock = ExamLock(heartbeat_timeout_sec=300)
    with lock:                      # installs hook, starts watchdog
        ...run exam...
                                  # hook released on exit
    lock.heartbeat()              # call periodically from your supervisor

CLI demo::

    python exam_lock.py lock --seconds 30 --heartbeat-timeout 10
    python exam_lock.py kiosk https://exam.example.com
"""

from __future__ import annotations

import atexit
import argparse
import ctypes
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import weakref
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "ExamLock",
    "lock_exam",
    "release_exam",
    "exam_heartbeat",
    "is_supported_platform",
    "key_combination_blocked",
    "launch_edge_kiosk",
    "edge_kiosk_command",
    "find_msedge",
]

log = logging.getLogger("exam_lock")

IS_WINDOWS: bool = sys.platform.startswith("win")

# ---------------------------------------------------------------------------
# Virtual-key / message constants (Win32, used on every platform by the
# shared filtering logic and its unit tests).
# ---------------------------------------------------------------------------
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_F4 = 0x73
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# Win32 hook / message constants (only meaningful on Windows, defined
# everywhere so the module imports cleanly on Linux/macOS).
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012


def is_supported_platform() -> bool:
    """True when a *real* Win32 low-level keyboard hook is available."""
    return IS_WINDOWS


def key_combination_blocked(
    vk_code: int,
    ctrl_down: bool = False,
    alt_down: bool = False,
    shift_down: bool = False,
    *,
    block_task_manager: bool = True,
) -> bool:
    """Pure decision function: should this key-down be swallowed?

    Shared by the real Windows hook procedure and the non-Windows simulator so
    both paths execute identical logic.  ``ctrl_down`` / ``alt_down`` /
    ``shift_down`` are the *async* modifier states sampled at hook time.
    """
    # Win key down: swallowing it also kills every Win+<x> shell shortcut.
    if vk_code in (VK_LWIN, VK_RWIN):
        return True
    # Alt+Tab (incl. Alt+Shift+Tab), Alt+Esc, Alt+F4.
    if alt_down and vk_code in (VK_TAB, VK_ESCAPE, VK_F4):
        return True
    # Ctrl+Esc opens the Start menu; Ctrl+Shift+Esc opens Task Manager.
    if ctrl_down and vk_code == VK_ESCAPE:
        return True
    return False


# ---------------------------------------------------------------------------
# Windows-only ctypes plumbing.
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes.wintypes as _wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _HHOOK = ctypes.c_void_p
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t
    _LRESULT = ctypes.c_ssize_t

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", _wt.DWORD),
            ("scanCode", _wt.DWORD),
            ("flags", _wt.DWORD),
            ("time", _wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _HOOK_PROC_LL = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)

    _user32.SetWindowsHookExW.restype = _HHOOK
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    _user32.UnhookWindowsHookEx.restype = _wt.BOOL
    _user32.UnhookWindowsHookEx.argtypes = [_HHOOK]
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.CallNextHookEx.argtypes = [_HHOOK, ctypes.c_int, _WPARAM, _LPARAM]
    _user32.GetMessageW.restype = ctypes.c_int  # NOT BOOL: -1 must survive
    _user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    _user32.PostThreadMessageW.restype = _wt.BOOL
    _user32.PostThreadMessageW.argtypes = [ctypes.c_uint, ctypes.c_uint, _WPARAM, _LPARAM]
    _kernel32.GetCurrentThreadId.restype = _wt.DWORD
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    _kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    # GetAsyncKeyState returns SHORT; bit 15 (0x8000) means "currently down".
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

    # -- SendInput plumbing: synthetic key-up injection (H3) --------------
    # Used when the lockdown is torn down while a key-down we swallowed is
    # still unreleased, so Win/Alt are never left logically stuck down.
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    MAPVK_VK_TO_VSC = 0

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", _wt.WORD),
            ("wScan", _wt.WORD),
            ("dwFlags", _wt.DWORD),
            ("time", _wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", _wt.LONG),
            ("dy", _wt.LONG),
            ("mouseData", _wt.DWORD),
            ("dwFlags", _wt.DWORD),
            ("time", _wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", _wt.DWORD),
            ("wParamL", _wt.WORD),
            ("wParamH", _wt.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", _wt.DWORD), ("ii", _INPUTUNION)]

    _user32.SendInput.restype = _wt.UINT
    _user32.SendInput.argtypes = [_wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.MapVirtualKeyW.restype = _wt.UINT
    _user32.MapVirtualKeyW.argtypes = [_wt.UINT, _wt.UINT]

    def _inject_key_up(vk: int) -> bool:
        """Queue a synthetic key-up for ``vk``; True when SendInput accepted it."""
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ii.ki.wVk = _wt.WORD(vk & 0xFFFF)
        inp.ii.ki.wScan = _wt.WORD(int(_user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFFFF)
        inp.ii.ki.dwFlags = _wt.DWORD(KEYEVENTF_KEYUP)
        return int(_user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))) == 1

    def _async_modifier_states() -> Tuple[bool, bool, bool]:
        """(ctrl, alt, shift) currently-down states via GetAsyncKeyState."""
        def down(vk: int) -> bool:
            return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

        return down(VK_CONTROL), down(VK_MENU), down(VK_SHIFT)


class _WindowsKeyboardHook:
    """Real WH_KEYBOARD_LL hook driven by a dedicated message-pump thread.

    The hook *must* be installed on the thread that pumps messages: Windows
    silently removes a low-level hook whose owning thread stops servicing its
    message queue (``LowLevelHooksTimeout``), so install + pump + unhook all
    live on one thread and are bracketed by events for the caller.
    """

    def __init__(self, owner: "ExamLock") -> None:
        self._owner = owner
        self._hook: Optional[int] = None
        self._cb = None  # ctypes callback; MUST stay referenced
        self._thread: Optional[threading.Thread] = None
        self._tid: int = 0
        self._installed = threading.Event()
        self._install_ok = False
        self._install_err = 0
        self._released = threading.Event()
        # Flipped by stop() *before* anything else: the pump checks it before
        # installing and right after installing, closing the race where the
        # hook would come up after stop() gave up waiting (H2).
        self._stopping = threading.Event()
        # In-memory-only error counter: the hook procedure must never log (H1).
        self._hook_errors = 0
        # vkCodes whose key-down we swallowed -> swallow matching key-up too,
        # so apps never see an orphaned release (e.g. Alt+F4 "close").
        self._blocked_ups: set = set()

    # -- lifecycle -----------------------------------------------------
    def start(self, install_timeout: float = 3.0) -> bool:
        self._thread = threading.Thread(target=self._pump, name="exam-lock-pump", daemon=True)
        self._thread.start()
        if not self._installed.wait(install_timeout):
            log.error("timed out waiting for keyboard-hook installation")
            self.stop()
            return False
        if not self._install_ok:
            log.error("SetWindowsHookExW(WH_KEYBOARD_LL) failed (GetLastError=%s)",
                      self._install_err)
            self.stop()
            return False
        return True

    def stop(self, join_timeout: float = 3.0) -> None:
        # Flip the stopping flag FIRST (H2): a pump that has not installed
        # yet skips the install, and one that installs mid-teardown unhooks
        # in its own finally, so the hook can never outlive this call.
        self._stopping.set()
        thread = self._thread
        if thread is None:
            self._blocked_ups.clear()
            self._tid = 0
            return
        deadline = time.monotonic() + max(join_timeout, 0.1)
        quit_posted = False
        # Wait on _released rather than trusting join() alone: WM_QUIT needs
        # the pump's thread id, which the pump publishes on its first line,
        # so keep re-checking until either it is known (post once) or the
        # bounded wait expires.
        while not self._released.wait(0.05):
            if not quit_posted and self._tid:
                try:
                    _user32.PostThreadMessageW(
                        _wt.DWORD(self._tid), WM_QUIT, _WPARAM(0), _LPARAM(0)
                    )
                except Exception:  # pragma: no cover - defensive
                    log.debug("PostThreadMessageW(WM_QUIT) failed", exc_info=True)
                quit_posted = True
            if time.monotonic() >= deadline:
                break
        if not self._released.is_set():
            log.warning("exam-lock pump did not release the hook within %.1fs "
                        "(stopping flag detaches it as soon as the thread runs)",
                        join_timeout)
        thread.join(0.5)
        self._thread = None
        self._tid = 0
        # H3: with the hook gone, synthesise key-ups for every swallowed
        # key-down that never saw its release (Win/Alt must not stay stuck).
        self._release_swallowed_keys()

    def _release_swallowed_keys(self) -> None:
        pending, self._blocked_ups = set(self._blocked_ups), set()
        if not pending:
            return
        if not self._released.is_set():
            # Hook still live: it would swallow our own injected releases.
            # The stopping flag detaches it shortly; process-exit cleanup is
            # the last resort.
            return
        for vk in sorted(pending):
            try:
                if not _inject_key_up(vk):
                    log.debug("SendInput rejected synthetic key-up vk=0x%02X", vk)
            except Exception:  # pragma: no cover - defensive
                log.debug("failed to inject synthetic key-up vk=0x%02X", vk, exc_info=True)

    # -- pump thread -----------------------------------------------------
    def _pump(self) -> None:
        try:
            # Publish the thread id on the very first line so stop() can
            # always reach this message queue with WM_QUIT (H2).
            self._tid = int(_kernel32.GetCurrentThreadId())
            if self._stopping.is_set():
                # stop() already ran while we were starting up: report the
                # failure; the finally below completes the teardown handshake.
                self._install_ok = False
                self._installed.set()
                return
            hmod = _kernel32.GetModuleHandleW(None)
            self._cb = _HOOK_PROC_LL(self._hook_proc)
            handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, ctypes.cast(self._cb, ctypes.c_void_p), hmod, 0)
            if not handle:
                # GetLastError is thread-local: capture it HERE, on the pump
                # thread, for the caller's error log.
                self._install_err = ctypes.get_last_error()
                self._install_ok = False
                self._installed.set()
                return
            self._hook = handle
            self._install_ok = True
            self._installed.set()
            if self._stopping.is_set():
                # Torn down while we were installing: finally unhooks (H2).
                return

            msg = _wt.MSG()
            while True:
                # -1 -> error, 0 -> WM_QUIT: either way, tear the hook down.
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    if ret == -1:
                        log.error("exam-lock pump GetMessageW failed (err=%s), releasing hook",
                                  ctypes.get_last_error())
                    break
                # No window on this thread: LL-hook callbacks are delivered by
                # the OS while we retrieve messages; nothing to dispatch.
        finally:
            try:
                if self._hook:
                    _user32.UnhookWindowsHookEx(_HHOOK(self._hook))
                    self._hook = None
            except Exception:  # pragma: no cover - defensive
                log.exception("UnhookWindowsHookEx failed")
            self._released.set()

    # -- hook procedure ----------------------------------------------------
    def _hook_proc(self, n_code: int, w_param, l_param) -> int:
        # HARD RULE (H1): this procedure runs synchronously inside every
        # thread that receives keyboard input.  Logging, string formatting
        # or any I/O here can exceed the OS ``LowLevelHooksTimeout``, after
        # which Windows silently drops the hook and the exam silently loses
        # its lockdown.  Keep this path strictly in-memory and allocation-
        # free apart from the two fixed-size ctypes reads.
        try:
            if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                ctrl, alt, shift = _async_modifier_states()
                if is_up:
                    if vk in self._blocked_ups:
                        self._blocked_ups.discard(vk)
                        self._owner._note_blocked(vk)
                        return 1  # swallow: event is not passed on
                elif key_combination_blocked(vk, ctrl, alt, shift,
                                             block_task_manager=self._owner.block_task_manager):
                    self._blocked_ups.add(vk)
                    self._owner._note_blocked(vk)
                    return 1  # swallow: event is not passed on
                else:
                    self._owner._note_passed(vk)
        except Exception:  # pragma: no cover - never crash the input path
            # In-memory bookkeeping only - NO logging here (H1); a hook
            # error must fail open (key passes) without touching the OS.
            self._hook_errors += 1
        return int(_user32.CallNextHookEx(_HHOOK(self._hook), n_code, w_param, l_param))


class _SimulatedKeyboardHook:
    """Non-Windows stand-in with identical filtering logic (pytest friendly).

    ``simulate_key(vk, ctrl=..., alt=...)`` feeds a virtual key event through
    the exact same decision function the Windows hook procedure uses and
    reports whether it was swallowed, so lockdown behaviour is testable on
    Linux/macOS CI.
    """

    def __init__(self, owner: "ExamLock") -> None:
        self._owner = owner
        self._running = False
        self._blocked_ups: set = set()

    def start(self, install_timeout: float = 3.0) -> bool:
        log.info("non-Windows platform (%s): using SIMULATED exam lockdown hook", sys.platform)
        self._running = True
        return True

    def stop(self, join_timeout: float = 3.0) -> None:
        self._running = False
        self._blocked_ups.clear()

    # -- simulation API (also handy for manual demos) -----------------------
    @property
    def running(self) -> bool:
        return self._running

    def simulate_key(self, vk_code: int, *, ctrl: bool = False, alt: bool = False,
                     shift: bool = False, key_up: bool = False) -> bool:
        """Feed one virtual key event; True => event was swallowed."""
        if not self._running:
            return False
        owner = self._owner
        if key_up:
            if vk_code in self._blocked_ups:
                self._blocked_ups.discard(vk_code)
                owner._note_blocked(vk_code)
                return True
            owner._note_passed(vk_code)
            return False
        if key_combination_blocked(vk_code, ctrl, alt, shift,
                                   block_task_manager=owner.block_task_manager):
            self._blocked_ups.add(vk_code)
            owner._note_blocked(vk_code)
            return True
        owner._note_passed(vk_code)
        return False


# ---------------------------------------------------------------------------
# Public API: ExamLock
# ---------------------------------------------------------------------------
class ExamLock:
    """Windows exam lockdown controller (keyboard hook + watchdog).

    Parameters
    ----------
    heartbeat_timeout_sec:
        Auto-release the lock if :meth:`heartbeat` has not been called within
        this many seconds (protects against a crashed exam supervisor
        permanently bricking the student's keyboard).  ``None`` disables the
        watchdog (lock lasts until :meth:`unlock`).
    block_task_manager:
        Reserved switch (Ctrl+Shift+Esc is always blocked together with
        Ctrl+Esc, which opens the same Start menu path).
    """

    _live: "weakref.WeakSet[ExamLock]" = weakref.WeakSet()

    def __init__(self, heartbeat_timeout_sec: Optional[float] = None,
                 *, block_task_manager: bool = True) -> None:
        if heartbeat_timeout_sec is not None and heartbeat_timeout_sec <= 0:
            raise ValueError("heartbeat_timeout_sec must be positive or None")
        self.block_task_manager = block_task_manager
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.auto_released = False          # set when the watchdog let go
        self.blocked_count = 0
        self.passed_count = 0
        self._active = False
        self._lock = threading.RLock()
        self._backend = None                # _WindowsKeyboardHook | _SimulatedKeyboardHook
        self._last_beat = 0.0               # time.monotonic()
        self._watchdog: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._saved_signals: Dict[int, object] = {}
        ExamLock._live.add(self)

    # -- state -------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def simulated(self) -> bool:
        """True when running on a non-Windows fallback (simulator)."""
        return self._active and not IS_WINDOWS

    # -- lifecycle -----------------------------------------------------------
    def lock(self) -> bool:
        """Install the keyboard hook + watchdog.  Returns True on success.

        Idempotent: calling it while already active is a no-op returning True.
        """
        with self._lock:
            if self._active:
                return True
            if IS_WINDOWS:
                self._backend = _WindowsKeyboardHook(self)
            else:
                self._backend = _SimulatedKeyboardHook(self)
            if not self._backend.start():
                self._backend = None
                return False
            # Flush any signal-handler restore deferred by a previous
            # watchdog-thread auto-release (we are able to restore here only
            # if lock() itself is called from the main thread).
            self._restore_signal_handlers()
            self._active = True
            self.auto_released = False
            self._last_beat = time.monotonic()
            self._start_watchdog()
            self._install_signal_handlers()
            log.info("exam lockdown ENGAGED (%s, heartbeat_timeout=%s)",
                     "win32 WH_KEYBOARD_LL" if IS_WINDOWS else "simulated",
                     self.heartbeat_timeout_sec)
            return True

    def unlock(self) -> None:
        """Release the keyboard hook and stop the watchdog.  Safe anytime."""
        with self._lock:
            if not self._active:
                return
            self._active = False  # flip first: heartbeat/watchdog must not re-arm
            self._watchdog_stop.set()
            wd, self._watchdog = self._watchdog, None
            # When the watchdog itself triggers auto-release, never join self.
            if wd is not None and wd.is_alive() and wd is not threading.current_thread():
                wd.join(3.0)
            try:
                if self._backend is not None:
                    self._backend.stop()
                    self._backend = None
            finally:
                self._restore_signal_handlers()
                log.info("exam lockdown RELEASED (blocked=%d, passed=%d%s)",
                         self.blocked_count, self.passed_count,
                         ", auto by watchdog" if self.auto_released else "")

    # -- heartbeat / watchdog --------------------------------------------------
    def heartbeat(self) -> None:
        """Call regularly while the exam is legitimately running to keep the lock."""
        if self._active:
            self._last_beat = time.monotonic()

    def _start_watchdog(self) -> None:
        if self.heartbeat_timeout_sec is None:
            return
        self._watchdog_stop.clear()
        self._watchdog = threading.Thread(target=self._watchdog_loop,
                                          name="exam-lock-watchdog", daemon=True)
        self._watchdog.start()

    def _watchdog_loop(self) -> None:
        timeout = float(self.heartbeat_timeout_sec or 0)
        poll = min(1.0, timeout / 4.0) if timeout > 0 else 1.0
        while not self._watchdog_stop.wait(poll):
            if not self._active:
                return
            stale = time.monotonic() - self._last_beat
            if stale > timeout:
                log.critical("exam-lock heartbeat stale for %.1fs (> %.1fs) - "
                             "auto-releasing keyboard hook", stale, timeout)
                self.auto_released = True
                self.unlock()
                return

    # -- signal handlers / stats -------------------------------------------
    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return  # signal.signal only works from the main thread
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                if sig in self._saved_signals:
                    continue  # already chained, do not double-wrap
                self._saved_signals[sig] = signal.getsignal(sig)
                signal.signal(sig, self._make_signal_handler(sig))
            except (OSError, ValueError):  # pragma: no cover - exotic platforms
                log.debug("could not install handler for %s", sig, exc_info=True)

    def _make_signal_handler(self, sig: int) -> Callable:
        fallback_saved = self._saved_signals.get(sig)

        def handler(signum, frame):  # noqa: ANN001 - signal API signature
            saved = self._saved_signals.get(sig, fallback_saved)
            if self._active:
                self.unlock()
            elif self._saved_signals:
                # Deferred restore: a watchdog-thread unlock could not call
                # signal.signal() off the main thread; we run there now.
                self._restore_signal_handlers()
            if sig == signal.SIGINT:
                if saved is signal.SIG_IGN:
                    return
                if callable(saved) and saved is not signal.SIG_DFL:
                    saved(signum, frame)
                    return
                raise KeyboardInterrupt
            # SIGTERM: emulate default termination *after* cleanup.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        return handler

    def _restore_signal_handlers(self) -> None:
        if not self._saved_signals:
            return
        if threading.current_thread() is not threading.main_thread():
            # CPython forbids signal.signal() outside the main thread: keep
            # the saved handlers and let our wrapper restore them the next
            # time it fires there (wrapper chaining keeps behaviour intact
            # until then).  atexit unlock runs on the main thread too.
            log.debug("unlock from non-main thread: deferring signal restore")
            return
        for sig, saved in self._saved_signals.items():
            try:
                signal.signal(sig, saved)  # type: ignore[arg-type]
            except (OSError, ValueError, TypeError):  # pragma: no cover
                pass
        self._saved_signals.clear()

    # -- counters (called by hook backends) -------------------------------
    def _note_blocked(self, vk: int) -> None:
        # Pure in-memory counter: this is invoked from inside the low-level
        # hook callback, where any logging/IO is forbidden (H1).  Consumers
        # that want per-key detail poll blocked_count or use the simulator.
        self.blocked_count += 1

    def _note_passed(self, vk: int) -> None:
        self.passed_count += 1

    # -- convenience -----------------------------------------------------------
    def __enter__(self) -> "ExamLock":
        if not self.lock():
            raise RuntimeError("failed to engage exam lockdown")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.unlock()
        return False

    # Simulation shim: on non-Windows, drive the same logic for self-tests.
    def simulate_key(self, vk_code: int, **mods) -> Optional[bool]:
        """Simulate one key event against the live lockdown.

        Returns True (swallowed) / False (delivered) while the simulated hook
        is running, or ``None`` when no simulator is active (real Windows, or
        not currently locked).  For the pure decision table use
        :func:`key_combination_blocked` directly.
        """
        backend = self._backend
        if isinstance(backend, _SimulatedKeyboardHook):
            return backend.simulate_key(vk_code, **mods)
        return None


# ---------------------------------------------------------------------------
# Module-level convenience API (single shared lock instance)
# ---------------------------------------------------------------------------
_GLOBAL_LOCK: Optional[ExamLock] = None
_GLOBAL_LOCK_INIT = threading.Lock()


def lock_exam(heartbeat_timeout_sec: Optional[float] = None,
              *, block_task_manager: bool = True) -> bool:
    """Engage the shared module-level lockdown. Returns success."""
    global _GLOBAL_LOCK
    with _GLOBAL_LOCK_INIT:
        if _GLOBAL_LOCK is None:
            _GLOBAL_LOCK = ExamLock(heartbeat_timeout_sec=heartbeat_timeout_sec,
                                    block_task_manager=block_task_manager)
        return _GLOBAL_LOCK.lock()


def exam_heartbeat() -> None:
    """Beat for the shared lockdown (no-op if not locked)."""
    if _GLOBAL_LOCK is not None:
        _GLOBAL_LOCK.heartbeat()


def release_exam() -> None:
    """Release the shared module-level lockdown."""
    if _GLOBAL_LOCK is not None:
        _GLOBAL_LOCK.unlock()


def _release_all_at_exit() -> None:
    for inst in list(ExamLock._live):
        try:
            inst.unlock()
        except Exception:  # pragma: no cover - interpreter shutdown
            pass


atexit.register(_release_all_at_exit)


# ---------------------------------------------------------------------------
# Microsoft Edge kiosk launcher
# ---------------------------------------------------------------------------
_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
)

#: Only web schemes are legitimate exam targets; file:// and about: are
#: rejected so a locked exam page cannot probe local paths or internal
#: browser pages (H5).
_ALLOWED_KIOSK_SCHEMES = ("http", "https")


def _validate_kiosk_url(url: str) -> None:
    """Strictly validate a kiosk target URL (H5).

    * No control/space characters anywhere: a stray ``\\n``, ``\\t`` or space
      is the classic vector for smuggling extra Edge command-line switches
      past a naive scheme prefix check (chars < 0x21 cover NUL/space/CR/LF;
      0x7F is DEL).
    * Scheme must be exactly http or https (case-insensitive).
    * A network location (host) is mandatory - ``https:///no-host`` is not a
      navigable exam target.
    """
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url):
        raise ValueError("kiosk URL must not contain control or space characters")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in _ALLOWED_KIOSK_SCHEMES:
        raise ValueError(f"refusing to open non-exam URL scheme: {url!r}")
    if not parsed.netloc:
        raise ValueError(f"kiosk URL must include a host name: {url!r}")


def find_msedge() -> Optional[str]:
    """Locate msedge.exe, standard install locations BEFORE PATH (H5).

    PATH entries are frequently user-writable on shared classroom machines,
    so trusting PATH first would let any local account drop a trojan
    ``msedge.exe`` straight into the kiosk.  Only when no signed standard
    install exists do we fall back to PATH resolution.
    """
    for candidate in _EDGE_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("msedge")


def edge_kiosk_command(url: str, exe: str = "msedge.exe",
                       *, user_data_dir: Optional[str] = None,
                       extra_args: Optional[Sequence[str]] = None) -> List[str]:
    """Build the Edge fullscreen-kiosk argv (pure, unit-testable)."""
    _validate_kiosk_url(url)
    cmd = [
        exe,
        f"--kiosk={url}",
        "--edge-kiosk-type=fullscreen",  # official Edge kiosk mode (no chrome)
        "--no-first-run",
        "--disable-pinch",               # no accidental trackpad zoom
        "--overscroll-history-navigation=0",
    ]
    if user_data_dir:
        # Dedicated profile: keeps sign-in / restore bubbles out of the exam.
        cmd.append(f"--user-data-dir={user_data_dir}")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def launch_edge_kiosk(url: str, *, user_data_dir: Optional[str] = None,
                      extra_args: Optional[Sequence[str]] = None) -> Optional[subprocess.Popen]:
    """Spawn Microsoft Edge in fullscreen kiosk mode.

    Returns the :class:`subprocess.Popen` handle (caller may ``.wait()`` on it
    to know when the exam session ends), or ``None`` when Edge was not found
    or we are not on Windows (graceful dev fallback: the command is logged
    instead of executed).
    """
    cmd = edge_kiosk_command(url, user_data_dir=user_data_dir, extra_args=extra_args)
    if not IS_WINDOWS:
        log.warning("non-Windows platform (%s): would have launched: %s",
                    sys.platform, " ".join(shlex.quote(p) for p in cmd))
        return None
    exe = find_msedge()
    if not exe:
        log.error("Microsoft Edge (msedge.exe) not found; cannot start kiosk mode")
        return None
    cmd[0] = exe
    log.info("launching Edge kiosk: %s", url)
    try:
        return subprocess.Popen(cmd)  # noqa: S603 - fixed exe, validated URL scheme
    except OSError as exc:
        log.error("failed to launch Edge kiosk: %s", exc)
        return None


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------
def _main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="exam_lock", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lock = sub.add_parser("lock", help="engage lockdown (demo: Ctrl+C or timeout releases)")
    p_lock.add_argument("--seconds", type=float, default=None,
                        help="auto-release after N seconds (default: until Ctrl+C)")
    p_lock.add_argument("--heartbeat-timeout", type=float, default=None,
                        help="auto-release if no heartbeat within N seconds")
    p_lock.add_argument("--beat-every", type=float, default=None,
                        help="simulate a heartbeat every N seconds (demo)")

    p_kiosk = sub.add_parser("kiosk", help="launch Microsoft Edge fullscreen kiosk")
    p_kiosk.add_argument("url")

    args = parser.parse_args(argv)

    if args.cmd == "kiosk":
        proc = launch_edge_kiosk(args.url)
        if proc is None:
            return 1
        return proc.wait()

    lock = ExamLock(heartbeat_timeout_sec=args.heartbeat_timeout)
    if not lock.lock():
        return 1
    deadline = (time.monotonic() + args.seconds) if args.seconds else None
    next_beat = None
    try:
        while lock.active:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if args.beat_every is not None and (next_beat is None or now >= next_beat):
                lock.heartbeat()
                next_beat = now + args.beat_every
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        lock.unlock()
    print(f"blocked={lock.blocked_count} passed={lock.passed_count} "
          f"auto_released={lock.auto_released}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
