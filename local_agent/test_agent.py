#!/usr/bin/env python3
"""End-to-end verification test for CodingPass Local Agent.

Covers:
  * compiler detection / compile / run / TLE happy paths
  * MAX_OUTPUT_BYTES output truncation (unbounded student output)
  * process-group cleanup after a timeout (no orphan children left behind)
  * Origin whitelist (CSWSH): 403 for untrusted Origin, 101 for localhost
  * 'run_batch' over the WebSocket: many inputs, a single compile
"""

import asyncio
import json
import os
import struct
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent as agent_module  # noqa: E402
from agent import (  # noqa: E402
    MAX_OUTPUT_BYTES,
    execute_local_batch,
    execute_local_code,
    find_compiler,
    get_compiler_info,
    is_allowed_origin,
)

TEST_PORT = 18765

ADD_CPP = """#include <iostream>
using namespace std;
int main() {
    long long a, b;
    if (cin >> a >> b) cout << (a + b) << endl;
    return 0;
}
"""


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_unit() -> None:
    print("[1] Compiler detection:")
    print("    C compiler:", find_compiler("c"))
    print("    C++ compiler:", find_compiler("cpp"))
    print("    Info:", get_compiler_info())

    print("\n[2] Execution test (C++ success):")
    res = execute_local_code(ADD_CPP, "cpp", "10 20\n", 3.0)
    print("    Status:", res["status"], "| Output:", repr(res["stdout"]))
    assert res["status"] == "success", res
    assert res["stdout"].strip() == "30", res

    print("\n[3] Execution test (C++ compile error):")
    res_bad = execute_local_code("int main() { syntax error }", "cpp", "", 3.0)
    print("    Status:", res_bad["status"])
    assert res_bad["status"] == "compile_error", res_bad

    print("\n[4] Execution test (TLE):")
    res_tle = execute_local_code("int main() { while(1); }", "cpp", "", 1.0)
    print("    Status:", res_tle["status"], "|", res_tle["stderr"])
    assert res_tle["status"] == "time_limit_exceeded", res_tle

    print("\n[5] Output truncation (C program printing >300KB):")
    chatty = (
        "#include <stdio.h>\n"
        "int main() {\n"
        "    int i;\n"
        "    for (i = 0; i < 40000; i++) puts(\"abcdefgh\");\n"  # ~351 KB
        "    return 0;\n"
        "}\n"
    )
    res_big = execute_local_code(chatty, "c", "", 15.0)
    print(
        "    Status:", res_big["status"],
        "| captured stdout bytes:", len(res_big["stdout"]),
        "| truncated:", res_big.get("stdout_truncated"),
    )
    assert res_big["status"] == "success", res_big
    assert res_big["stdout_truncated"] is True, res_big
    assert res_big["truncated"] is True, res_big
    assert len(res_big["stdout"]) < MAX_OUTPUT_BYTES + 256, len(res_big["stdout"])
    assert "truncated" in res_big["stdout"], res_big["stdout"][-200:]

    print("\n[6] Batch execution compiles exactly once (unit level):")
    compile_calls: List[str] = []
    run_calls: List[str] = []
    real_compile = agent_module._compile_source
    real_run = agent_module._run_binary

    def spy_compile(*args, **kwargs):
        compile_calls.append("compile")
        return real_compile(*args, **kwargs)

    def spy_run(*args, **kwargs):
        run_calls.append("run")
        return real_run(*args, **kwargs)

    agent_module._compile_source = spy_compile
    agent_module._run_binary = spy_run
    try:
        batch = execute_local_batch(ADD_CPP, "cpp", ["1 2\n", "3 4\n", "100 200\n"], 3.0)
    finally:
        agent_module._compile_source = real_compile
        agent_module._run_binary = real_run

    print("    Status:", batch["status"], "| compiles:", len(compile_calls),
          "| runs:", len(run_calls), "| compile_time_ms:", batch["compile_time_ms"])
    assert batch["status"] == "ok", batch
    assert len(compile_calls) == 1, compile_calls
    assert len(run_calls) == 3, run_calls
    assert batch["cases_executed"] == 3, batch
    assert [r["stdout"].strip() for r in batch["results"]] == ["3", "7", "300"], batch
    assert [r["index"] for r in batch["results"]] == [0, 1, 2], batch

    print("\n[7] Batch with a compile error returns compile_error / no results:")
    bad_batch = execute_local_batch("int main(){ @@ }", "c", ["x\n"], 3.0)
    print("    Status:", bad_batch["status"], "| results:", len(bad_batch["results"]))
    assert bad_batch["status"] == "compile_error", bad_batch
    assert bad_batch["results"] == [], bad_batch

    print("\n[8] Origin whitelist (is_allowed_origin):")
    must_allow = [
        None,
        "",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://[::1]:5173",
        "http://192.168.1.50:8765",
        "http://10.1.2.3",
        "http://172.16.0.9:3000",
    ]
    must_deny = [
        "https://evil.example.com",
        "null",
        "http://8.8.8.8",
        "https://classroom.attacker.io",
        "http://localhost.evil.example.com",
        "http://10.attacker.example.com",
        "http://192.168.evil.example.com",
        "http://172.attacker.example.com",
        # Only 172.16.0.0/12 is private. The rest of 172/8 is routable public
        # space (172.104.x.x is a hosting provider, 172.217.x.x belongs to Google),
        # so a page served from there must NOT be trusted with local code execution.
        "http://172.104.1.2",
        "https://172.217.5.1",
        "http://172.32.0.1:8765",
        # CGNAT-ish and link-local forms of a public address stay untrusted too.
        "http://[::ffff:8.8.8.8]",
    ]
    for origin in must_allow:
        assert is_allowed_origin(origin), origin
    for origin in must_deny:
        assert not is_allowed_origin(origin), origin
    print("    loopback/private-allow / public-deny matrix passed")

    os.environ["CODINGPASS_ALLOWED_ORIGINS"] = "https://class.example.org, http://lab.local"
    try:
        assert is_allowed_origin("https://class.example.org"), "env allow-list entry rejected"
        assert is_allowed_origin("http://lab.local:8080"), "env host allow-list rejected"
        assert not is_allowed_origin("https://other.example.org"), "env allow-list leaked"
        print("    CODINGPASS_ALLOWED_ORIGINS allow-list passed")
    finally:
        os.environ.pop("CODINGPASS_ALLOWED_ORIGINS", None)

    print("\nAll unit tests passed successfully!")


def _orphan_scan() -> List[str]:
    """Return lingering sandbox processes (best effort, POSIX only)."""
    try:
        ps = subprocess.run(
            ["ps", "-eo", "pid,args"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
        ).stdout
    except Exception as exc:  # noqa: BLE001 - 'ps' unavailable: cannot verify
        print("    ps unavailable, skipped orphan check:", exc)
        return []
    return [
        line for line in ps.splitlines()
        if "cp-agent-" in line and "solution" in line
    ]


def test_process_group_cleanup() -> None:
    """Controlled 2-fork bomb that hangs: after TLE nothing may survive.

    Kept tiny on purpose (2 forks) - see AGENTS.md sandbox safety rules.
    """
    if os.name != "posix":
        print("\n[9] Process-group cleanup: skipped (POSIX only)")
        return

    print("\n[9] Process-group cleanup after TLE (2 forks + infinite loop):")
    code = (
        "#include <unistd.h>\n"
        "int main() {\n"
        "    fork(); fork();\n"
        "    for (;;) { sleep(1); }\n"
        "    return 0;\n"
        "}\n"
    )
    started = time.perf_counter()
    res = execute_local_code(code, "c", "", 1.0)
    elapsed = time.perf_counter() - started
    print(f"    Status: {res['status']} | wall time: {elapsed:.2f}s")
    assert res["status"] == "time_limit_exceeded", res

    time.sleep(0.5)
    leftovers = _orphan_scan()
    assert not leftovers, f"orphan processes survived the killpg: {leftovers}"
    assert elapsed < 8.0, elapsed
    print("    No orphan 'solution' processes survived the process-group kill")

    print("\n[9b] Orphan keeping the stdout pipe open after the child exited:")
    orphan_code = (
        "#include <unistd.h>\n"
        "#include <stdio.h>\n"
        "int main() {\n"
        "    if (fork() == 0) { sleep(30); _exit(0); }\n"  # inherits stdout
        "    printf(\"parent done\\n\");\n"
        "    return 0;\n"
        "}\n"
    )
    started = time.perf_counter()
    res_orphan = execute_local_code(orphan_code, "c", "", 5.0)
    elapsed = time.perf_counter() - started
    print(
        f"    Status: {res_orphan['status']} | stdout: {res_orphan['stdout']!r}"
        f" | wall time: {elapsed:.2f}s"
    )
    assert res_orphan["status"] == "success", res_orphan
    assert "parent done" in res_orphan["stdout"], res_orphan
    assert elapsed < 8.0, f"agent blocked on an orphan-held pipe: {elapsed:.2f}s"
    time.sleep(0.5)
    assert not _orphan_scan(), "orphan survived the grace-period group kill"
    print("    Orphan pipe-holder was killed instead of blocking the judge")

    print("\n[9c] Orphan that detaches with setsid() (unkillable by us):")
    detached_code = (
        "#include <unistd.h>\n"
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "int main() {\n"
        "    if (fork() == 0) { setsid(); sleep(2); _exit(0); }\n"
        "    printf(\"detached-parent done\\n\");\n"
        "    return 0;\n"
        "}\n"
    )
    started = time.perf_counter()
    res_detached = execute_local_code(detached_code, "c", "", 5.0)
    elapsed = time.perf_counter() - started
    print(
        f"    Status: {res_detached['status']} | stdout: {res_detached['stdout']!r}"
        f" | wall time: {elapsed:.2f}s"
    )
    # The detached descendant leaves our process group, so no kill can reach it -
    # but it must never be able to block the agent either. Closing a stream that a
    # still-blocked reader owns used to hang here forever.
    assert res_detached["status"] == "success", res_detached
    assert "detached-parent done" in res_detached["stdout"], res_detached
    assert elapsed < 8.0, f"agent blocked on a detached pipe-holder: {elapsed:.2f}s"
    time.sleep(2.5)  # let the short-lived detached child exit before scanning
    assert not _orphan_scan(), "detached orphan outlived its own sleep"
    print("    Detached pipe-holder could not wedge the agent")


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------


def make_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    mask = b"\x12\x34\x56\x78"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    if length < 126:
        header = bytearray([0x81, 0x80 | length])
    elif length <= 0xFFFF:
        header = bytearray([0x81, 0x80 | 126]) + struct.pack("!H", length)
    else:
        header = bytearray([0x81, 0x80 | 127]) + struct.pack("!Q", length)
    return header + mask + masked


async def read_frame(reader: asyncio.StreamReader) -> Dict[str, object]:
    head = await reader.readexactly(2)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", (await reader.readexactly(2)))[0]
    elif length == 127:
        length = struct.unpack("!Q", (await reader.readexactly(8)))[0]
    body = await reader.readexactly(length)
    return json.loads(body.decode("utf-8"))


async def ws_handshake(
    origin: Optional[str],
    port: int = TEST_PORT,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    """Open a TCP connection, send an HTTP upgrade (optionally with Origin)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [
        "GET / HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin1"))
    await writer.drain()
    try:
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
    except asyncio.IncompleteReadError as exc:
        response = exc.partial
    return reader, writer, response


async def send_and_read(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
    payload: Dict[str, object],
) -> Dict[str, object]:
    writer.write(make_frame(json.dumps(payload)))
    await writer.drain()
    return await asyncio.wait_for(read_frame(reader), timeout=60.0)


# ---------------------------------------------------------------------------
# WebSocket integration tests
# ---------------------------------------------------------------------------


async def test_websocket_server() -> None:
    print("\n[10] WebSocket server integration test:")
    agent_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
    proc = subprocess.Popen(
        [sys.executable, agent_file],
        env=dict(os.environ, CODINGPASS_AGENT_PORT=str(TEST_PORT)),
    )
    time.sleep(1.0)

    try:
        # --- Untrusted browser Origin must be refused with HTTP 403 ---------
        _, writer_bad, resp_evil = await ws_handshake("https://evil.example.com")
        print("    Untrusted Origin response:", resp_evil.splitlines()[0].decode("latin1").strip())
        assert b"403 Forbidden" in resp_evil, resp_evil
        writer_bad.close()

        # --- Allowed localhost Origin gets the 101 upgrade ------------------
        reader, writer, resp_ok = await ws_handshake("http://127.0.0.1:5173")
        print("    Localhost Origin response:", resp_ok.splitlines()[0].decode("latin1").strip())
        assert b"101 Switching Protocols" in resp_ok, resp_ok
        print("    Handshake OK!")

        data = await send_and_read(writer, reader, {"type": "ping"})
        print("    Ping response:", data)
        assert data["type"] == "pong"
        assert "compiler" in data

        # --- Single run -----------------------------------------------------
        code = """#include <stdio.h>
int main() {
    printf("Hello from Local Agent!\\n");
    return 0;
}"""
        res_data = await send_and_read(
            writer, reader, {"type": "run", "id": "test-1", "code": code, "language": "c"}
        )
        print("    Run response:", res_data["status"], repr(res_data["stdout"]))
        assert res_data["type"] == "result"
        assert res_data["id"] == "test-1"
        assert res_data["status"] == "success"
        assert "Hello from Local Agent!" in res_data["stdout"]
        assert res_data["stdout_truncated"] is False

        # --- Batch run: 4 inputs, one compile -------------------------------
        batch_res = await send_and_read(
            writer,
            reader,
            {
                "type": "run_batch",
                "id": "batch-1",
                "code": ADD_CPP,
                "language": "cpp",
                "stdin_list": ["1 2\n", "10 20\n", "-5 5\n", "1000000 2000000\n"],
                "timeout_sec": 3.0,
            },
        )
        print(
            "    Batch response:", batch_res["type"], batch_res["status"],
          "| cases:", batch_res["cases_executed"], "| compiles:", batch_res["compile_time_ms"],
        )
        assert batch_res["type"] == "batch_result", batch_res
        assert batch_res["id"] == "batch-1", batch_res
        assert batch_res["status"] == "ok", batch_res
        assert batch_res["cases_executed"] == 4, batch_res
        assert len(batch_res["results"]) == 4, batch_res
        assert "compile_time_ms" in batch_res and "compile_time_ms" not in batch_res["results"][0]
        assert [r["stdout"].strip() for r in batch_res["results"]] == [
            "3", "30", "0", "3000000",
        ], batch_res
        assert all(r["status"] == "success" for r in batch_res["results"]), batch_res
        print("    run_batch (single compile, 4 cases) passed!")

        writer.close()
        await writer.wait_closed()

        # --- Mixed pass/fail batch still reports per-case status ------------
        reader2, writer2, resp2 = await ws_handshake("http://localhost:5173")
        assert b"101 Switching Protocols" in resp2, resp2
        mixed = await send_and_read(
            writer2,
            reader2,
            {
                "type": "run_batch",
                "id": "batch-2",
                "code": "int main(){ while(1); return 0; }",
                "language": "c",
                "stdin_list": ["", ""],
                "timeout_sec": 1.0,
            },
        )
        print("    Mixed batch:", mixed["status"], [r["status"] for r in mixed["results"]])
        assert mixed["status"] == "ok", mixed
        assert all(r["status"] == "time_limit_exceeded" for r in mixed["results"]), mixed
        writer2.close()
        await writer2.wait_closed()

        # --- Missing Origin (non-browser client) is still allowed -----------
        reader3, writer3, resp3 = await ws_handshake(None)
        assert b"101 Switching Protocols" in resp3, resp3
        print("    Missing Origin (CLI client) accepted with 101")
        writer3.close()
        await writer3.wait_closed()

        print("    WebSocket server integration test passed!")
    finally:
        proc.terminate()
        proc.wait()


async def main() -> None:
    test_unit()
    test_process_group_cleanup()
    await test_websocket_server()
    print("\n=== ALL LOCAL AGENT TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
