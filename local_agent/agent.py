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

Agent -> Client:
  {"type": "pong", "version": "1.0.0", "compiler": "gcc 13.2.0"}
  {"type": "result", "id": "req-1", "status": "success"|"compile_error"|"time_limit_exceeded"|"runtime_error",
   "stdout": "...", "stderr": "...", "compile_output": "...", "execution_time_ms": 1.2, "exit_code": 0}
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

HOST = os.environ.get("CODINGPASS_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODINGPASS_AGENT_PORT", "8765"))
VERSION = "1.0.0"
WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

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


def get_compiler_info() -> str:
    """Return compiler description for ping response."""
    c_comp = find_compiler("c")
    cpp_comp = find_compiler("cpp")
    if not cpp_comp and not c_comp:
        return "No C/C++ compiler found (install GCC or MinGW)"
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
        return line
    except Exception:
        return target


def execute_local_code(
    code: str,
    language: str,
    stdin_data: str = "",
    timeout_sec: float = 3.0,
) -> Dict[str, object]:
    """Compile and execute source code locally in a temporary directory."""
    compiler_path = find_compiler(language)
    if not compiler_path:
        return {
            "status": "compile_error",
            "stdout": "",
            "stderr": f"Local compiler not found for language '{language}'.",
            "compile_output": (
                f"Error: Neither '{'gcc' if language == 'c' else 'g++'}' was found in PATH.\n"
                "Please install GCC/Clang or extract w64devkit beside agent.py."
            ),
            "exit_code": 1,
            "execution_time_ms": 0.0,
            "timed_out": False,
        }

    ext = "c" if language == "c" else "cpp"
    binary_name = "solution.exe" if sys.platform == "win32" else "solution"

    with tempfile.TemporaryDirectory(prefix="cp-agent-") as tmp_dir:
        src_path = os.path.join(tmp_dir, f"source.{ext}")
        bin_path = os.path.join(tmp_dir, binary_name)

        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        # 1. Compile
        compile_cmd = [compiler_path, "-O2", src_path, "-o", bin_path]
        if language == "cpp":
            compile_cmd.append("-std=c++17")

        c_t0 = time.perf_counter()
        try:
            c_proc = subprocess.run(
                compile_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15.0,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "compile_error",
                "stdout": "",
                "stderr": "Compilation timed out after 15s.",
                "compile_output": "Compilation timed out.",
                "exit_code": 1,
                "execution_time_ms": 0.0,
                "timed_out": True,
            }

        compile_time_ms = round((time.perf_counter() - c_t0) * 1000.0, 2)
        compile_output = (c_proc.stdout + "\n" + c_proc.stderr).strip()

        if c_proc.returncode != 0:
            return {
                "status": "compile_error",
                "stdout": "",
                "stderr": "",
                "compile_output": compile_output,
                "exit_code": c_proc.returncode,
                "compile_time_ms": compile_time_ms,
                "execution_time_ms": 0.0,
                "timed_out": False,
            }

        # 2. Execute
        e_t0 = time.perf_counter()
        try:
            r_proc = subprocess.run(
                [bin_path],
                input=stdin_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
            )
            exec_time_ms = round((time.perf_counter() - e_t0) * 1000.0, 2)

            if r_proc.returncode == 0:
                status = "success"
            else:
                status = "runtime_error"

            return {
                "status": status,
                "stdout": r_proc.stdout,
                "stderr": r_proc.stderr,
                "compile_output": compile_output,
                "exit_code": r_proc.returncode,
                "compile_time_ms": compile_time_ms,
                "execution_time_ms": exec_time_ms,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            exec_time_ms = round((time.perf_counter() - e_t0) * 1000.0, 2)
            return {
                "status": "time_limit_exceeded",
                "stdout": "",
                "stderr": f"Execution exceeded wall-clock limit of {timeout_sec:.1f}s.",
                "compile_output": compile_output,
                "exit_code": -9,
                "compile_time_ms": compile_time_ms,
                "execution_time_ms": exec_time_ms,
                "timed_out": True,
            }


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
        """Read next WebSocket text frame."""
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
            return await self.recv_text()

        if length == 126:
            ext = await self.reader.readexactly(2)
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = await self.reader.readexactly(8)
            length = struct.unpack("!Q", ext)[0]

        mask = await self.reader.readexactly(4) if masked else None
        payload = await self.reader.readexactly(length)

        if masked and mask:
            payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))

        if opcode == 0x1:  # Text frame
            return payload.decode("utf-8", errors="replace")
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

            msg_type = data.get("type")

            if msg_type == "ping":
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "pong",
                            "version": VERSION,
                            "compiler": get_compiler_info(),
                        }
                    )
                )

            elif msg_type == "run":
                req_id = data.get("id")
                code = data.get("code", "")
                language = data.get("language", "cpp")
                stdin_data = data.get("stdin", "")
                timeout_sec = float(data.get("timeout_sec", 3.0))

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

            else:
                await ws.send_text(
                    json.dumps({"type": "error", "message": f"Unknown type: {msg_type}"})
                )

    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        logger.info("Connection closed for %s", peer)
        await ws.close()


async def main() -> None:
    logger.info("==================================================")
    logger.info("  CodingPass Local Agent (學生端本地編譯執行服務)")
    logger.info("  Version: %s", VERSION)
    logger.info("  Detected Compiler: %s", get_compiler_info())
    logger.info("  Listening on ws://%s:%d", HOST, PORT)
    logger.info("==================================================")

    server = await asyncio.start_server(handle_client, HOST, PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("CodingPass Local Agent stopped.")
