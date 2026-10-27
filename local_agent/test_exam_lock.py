#!/usr/bin/env python3
"""Tests for exam_lock (runs on any platform; pytest-compatible).

The decision table (``key_combination_blocked``), simulator, heartbeat
watchdog, signal-handler hygiene and kiosk command builder are all
platform-independent by design, so this suite is meaningful on Windows as
well as on Linux/macOS CI.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import exam_lock as el  # noqa: E402


# --------------------------------------------------------------------------
# Decision table
# --------------------------------------------------------------------------
@pytest.mark.parametrize("vk,mods", [
    (el.VK_LWIN, {}),
    (el.VK_RWIN, {}),
    (el.VK_TAB, {"alt_down": True}),                        # Alt+Tab
    (el.VK_TAB, {"alt_down": True, "shift_down": True}),    # Alt+Shift+Tab
    (el.VK_ESCAPE, {"alt_down": True}),                     # Alt+Esc
    (el.VK_ESCAPE, {"ctrl_down": True}),                    # Ctrl+Esc (Start)
    (el.VK_ESCAPE, {"ctrl_down": True, "shift_down": True}),  # Task Manager
    (el.VK_F4, {"alt_down": True}),                         # Alt+F4
])
def test_blocked_combinations(vk, mods):
    assert el.key_combination_blocked(vk, **mods)


@pytest.mark.parametrize("vk,mods", [
    (el.VK_F4, {}),                       # plain F4 is fine
    (el.VK_TAB, {}),                      # plain Tab is fine
    (el.VK_ESCAPE, {}),                   # plain Esc is fine
    (el.VK_CONTROL, {}),                  # bare modifiers must pass through
    (el.VK_MENU, {}),
    (0x41, {"ctrl_down": True}),          # Ctrl+A copy is fine
    (0x43, {"ctrl_down": True}),          # Ctrl+C copy is fine
    (0x56, {"ctrl_down": True}),          # Ctrl+V paste is fine
    (el.VK_F4, {"ctrl_down": True}),      # Ctrl+F4 is not Alt+F4
    (el.VK_TAB, {"ctrl_down": True}),     # Ctrl+Tab (browser tabs) is fine
])
def test_allowed_keys_pass_through(vk, mods):
    assert not el.key_combination_blocked(vk, **mods)


def test_platform_probe():
    assert el.is_supported_platform() == sys.platform.startswith("win")


# --------------------------------------------------------------------------
# Lock lifecycle (real hook on Windows, simulator elsewhere)
# --------------------------------------------------------------------------
def test_lock_unlock_cycle():
    lock = el.ExamLock()
    assert lock.lock()
    assert lock.lock() and lock.active          # idempotent re-lock
    lock.unlock()
    assert not lock.active
    lock.unlock()                                      # double unlock is safe


def test_context_manager_releases():
    with el.ExamLock() as lock:
        assert lock.active
    assert not lock.active


@pytest.mark.skipif(el.IS_WINDOWS, reason="simulator-only API")
def test_simulator_swallow_and_counters():
    lock = el.ExamLock()
    with lock:
        assert lock.simulated
        assert lock.simulate_key(el.VK_LWIN) is True            # down swallowed
        assert lock.simulate_key(el.VK_LWIN, key_up=True) is True  # matching up
        assert lock.simulate_key(el.VK_TAB, alt=True) is True
        assert lock.simulate_key(0x41) is False                 # typing passes
        assert lock.blocked_count == 3 and lock.passed_count == 1
    assert lock.simulate_key(el.VK_LWIN) is None                # released


@pytest.mark.skipif(el.IS_WINDOWS, reason="simulator-only API")
def test_events_pass_through_after_unlock():
    lock = el.ExamLock()
    lock.lock()
    lock.unlock()
    lock2 = el.ExamLock()
    with lock2:
        assert lock.blocked_count == 0
        assert lock2.simulate_key(el.VK_LWIN) is True           # fresh lock bites


# --------------------------------------------------------------------------
# Heartbeat watchdog
# --------------------------------------------------------------------------
def test_watchdog_keeps_lock_while_beating():
    lock = el.ExamLock(heartbeat_timeout_sec=0.5)
    lock.lock()
    try:
        for _ in range(8):
            lock.heartbeat()
            time.sleep(0.08)
        assert lock.active
    finally:
        lock.unlock()


def test_watchdog_auto_releases_on_stale_heartbeat():
    prev = signal.getsignal(signal.SIGINT)
    lock = el.ExamLock(heartbeat_timeout_sec=0.4)
    lock.lock()
    deadline = time.monotonic() + 3.0
    while lock.active and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not lock.active and lock.auto_released
    assert not [t for t in threading.enumerate() if t.name == "exam-lock-watchdog"]
    # The watchdog-thread unlock could not call signal.signal() (CPython
    # restricts it to the main thread): a re-lock from the main thread must
    # flush the deferred restore, then chain fresh handlers cleanly.
    assert lock.lock()
    assert signal.getsignal(signal.SIGINT) is not prev   # freshly wrapped
    lock.unlock()
    assert signal.getsignal(signal.SIGINT) is prev


def test_invalid_heartbeat_timeout():
    with pytest.raises(ValueError):
        el.ExamLock(heartbeat_timeout_sec=0)


# --------------------------------------------------------------------------
# Signal hygiene
# --------------------------------------------------------------------------
def test_main_thread_unlock_restores_handlers():
    prev = signal.getsignal(signal.SIGINT)
    lock = el.ExamLock()
    lock.lock()
    assert signal.getsignal(signal.SIGINT) is not prev
    lock.unlock()
    assert signal.getsignal(signal.SIGINT) is prev


def test_sigint_releases_lock_and_chains():
    prev = signal.getsignal(signal.SIGINT)
    lock = el.ExamLock()
    lock.lock()
    handler = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)
    assert not lock.active
    assert signal.getsignal(signal.SIGINT) is prev


def test_custom_prev_handler_is_chained_then_restored():
    seen = []

    def custom(signum, frame):
        seen.append(signum)

    old = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, custom)
    try:
        lock = el.ExamLock()
        lock.lock()
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert seen == [signal.SIGINT] and not lock.active
        lock.unlock()
        assert signal.getsignal(signal.SIGINT) is custom
    finally:
        signal.signal(signal.SIGINT, old)


# --------------------------------------------------------------------------
# Module-level convenience API
# --------------------------------------------------------------------------
def test_module_level_api():
    assert el.lock_exam()
    el.exam_heartbeat()
    el.release_exam()
    el.release_exam()  # idempotent


# --------------------------------------------------------------------------
# Edge kiosk
# --------------------------------------------------------------------------
def test_edge_kiosk_command_shape():
    cmd = el.edge_kiosk_command("https://exam.example.com")
    assert cmd[1] == "--kiosk=https://exam.example.com"
    assert "--edge-kiosk-type=fullscreen" in cmd
    assert "--no-first-run" in cmd


def test_edge_kiosk_command_user_data_dir_and_extra():
    cmd = el.edge_kiosk_command("https://x.test", exe=r"C:\edge\msedge.exe",
                                user_data_dir=r"C:\kiosk-profile",
                                extra_args=["--kiosk-autostart"])
    assert cmd[0] == r"C:\edge\msedge.exe"
    assert r"--user-data-dir=C:\kiosk-profile" in cmd
    assert "--kiosk-autostart" in cmd


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)", "ftp://evil/x", "cmd.exe /c calc", "", "data:text/html,x",
    # H5: host is mandatory, and only http(s) survive strict validation now.
    "file:///C:/exam/index.html", "about:blank", "https:///no-host", "http://",
])
def test_edge_kiosk_rejects_bad_schemes(bad_url):
    with pytest.raises(ValueError):
        el.edge_kiosk_command(bad_url)


@pytest.mark.parametrize("bad_url", [
    "https://exam.test\n--kiosk-autostart",   # newline switch smuggling
    "https://exam.test\tcalc",                # tab
    "https://exam.test evil",                 # bare space
    "https://exam.test\x00",                  # NUL
    "https://exam.test\x7f",                  # DEL
])
def test_edge_kiosk_rejects_control_characters(bad_url):
    with pytest.raises(ValueError):
        el.edge_kiosk_command(bad_url)


def test_edge_kiosk_accepts_https_and_uppercase_scheme():
    cmd = el.edge_kiosk_command("HTTP://EXAM.TEST/path")
    assert cmd[1] == "--kiosk=HTTP://EXAM.TEST/path"
    assert el.edge_kiosk_command("https://exam.example.com")


def test_find_msedge_prefers_install_locations_over_path(monkeypatch):
    checked = []

    def fake_isfile(path):
        checked.append(path)
        return path == el._EDGE_CANDIDATES[0]

    monkeypatch.setattr(el.os.path, "isfile", fake_isfile)
    # A trojan earlier in PATH must never win against a real install (H5).
    monkeypatch.setattr(el.shutil, "which", lambda name: "/tmp/user-writable/msedge.exe")
    assert el.find_msedge() == el._EDGE_CANDIDATES[0]
    assert checked[0] == el._EDGE_CANDIDATES[0]


def test_find_msedge_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(el.os.path, "isfile", lambda path: False)
    monkeypatch.setattr(el.shutil, "which", lambda name: f"/opt/msedge-{name}")
    assert el.find_msedge() == "/opt/msedge-msedge"


@pytest.mark.skipif(el.IS_WINDOWS, reason="non-Windows graceful fallback")
def test_launch_edge_kiosk_is_noop_off_windows():
    assert el.launch_edge_kiosk("https://exam.example.com") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
