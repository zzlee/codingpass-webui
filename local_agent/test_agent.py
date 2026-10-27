#!/usr/bin/env python3
"""End-to-end verification test for CodingPass Local Agent."""

import asyncio
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time

try:
    from agent import execute_local_code, find_compiler, get_compiler_info
except ImportError:
    from .agent import execute_local_code, find_compiler, get_compiler_info


def test_unit():
    print("[1] Compiler detection:")
    print("    C compiler:", find_compiler("c"))
    print("    C++ compiler:", find_compiler("cpp"))
    print("    Info:", get_compiler_info())

    print("\n[2] Execution test (C++ success):")
    code = """#include <iostream>
using namespace std;
int main() {
    int a, b;
    if (cin >> a >> b) {
        cout << (a + b) << endl;
    }
    return 0;
}
"""
    res = execute_local_code(code, "cpp", "10 20\n", 3.0)
    print("    Status:", res["status"])
    print("    Output:", repr(res["stdout"]))
    print("    Time:", res["execution_time_ms"], "ms")
    assert res["status"] == "success", res
    assert res["stdout"].strip() == "30", res

    print("\n[3] Execution test (C++ compile error):")
    bad_code = "int main() { syntax error }"
    res_bad = execute_local_code(bad_code, "cpp", "", 3.0)
    print("    Status:", res_bad["status"])
    assert res_bad["status"] == "compile_error", res_bad

    print("\n[4] Execution test (TLE):")
    tle_code = "int main() { while(1); }"
    res_tle = execute_local_code(tle_code, "cpp", "", 1.0)
    print("    Status:", res_tle["status"])
    assert res_tle["status"] == "time_limit_exceeded", res_tle

    print("\nAll unit tests passed successfully!")


async def test_websocket_server():
    print("\n[5] WebSocket server integration test:")
    agent_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
    # Start agent on port 18765 for testing
    proc = subprocess.Popen(
        [sys.executable, agent_file],
        env=dict(os.environ, CODINGPASS_AGENT_PORT="18765"),
    )
    time.sleep(1.0)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18765)
        # 1. Send Handshake
        req = (
            "GET / HTTP/1.1\r\n"
            "Host: 127.0.0.1:18765\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(req.encode("latin1"))
        await writer.drain()

        resp = await reader.readuntil(b"\r\n\r\n")
        assert b"101 Switching Protocols" in resp, resp
        print("    Handshake OK!")

        # 2. Send ping frame
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

        async def read_frame() -> dict:
            head = await reader.readexactly(2)
            length = head[1] & 0x7F
            if length == 126:
                ext = await reader.readexactly(2)
                length = struct.unpack("!H", ext)[0]
            elif length == 127:
                ext = await reader.readexactly(8)
                length = struct.unpack("!Q", ext)[0]
            body = await reader.readexactly(length)
            return json.loads(body.decode("utf-8"))

        writer.write(make_frame(json.dumps({"type": "ping"})))
        await writer.drain()

        data = await read_frame()
        print("    Ping response:", data)
        assert data["type"] == "pong"
        assert "compiler" in data

        # 3. Send run frame
        code = """#include <stdio.h>
int main() {
    printf("Hello from Local Agent!\\n");
    return 0;
}"""
        writer.write(make_frame(json.dumps({
            "type": "run",
            "id": "test-1",
            "code": code,
            "language": "c",
        })))
        await writer.drain()

        res_data = await read_frame()
        print("    Run response:", res_data["status"], repr(res_data["stdout"]))
        assert res_data["type"] == "result"
        assert res_data["id"] == "test-1"
        assert res_data["status"] == "success"
        assert "Hello from Local Agent!" in res_data["stdout"]

        writer.close()
        await writer.wait_closed()
        print("    WebSocket server integration test passed!")
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    test_unit()
    asyncio.run(test_websocket_server())
