"""Smoke tests for the judge backend (no pytest needed: `python test_judge.py`)."""

from __future__ import annotations

import json
import sys
import time

from fastapi.testclient import TestClient

import main
import compiler

client = TestClient(main.app)
client.__enter__()  # run lifespan handlers
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --- health --------------------------------------------------------------- #
resp = client.get("/api/health")
body = resp.json()
check("health 200", resp.status_code == 200, resp.status_code)
check("health has gcc_version", body.get("gcc_version", "").startswith("gcc"), body.get("gcc_version"))
check(
    "health has g++_version key",
    body.get("g++_version", "").startswith("g++"),
    json.dumps(list(body)),
)
check("health status ok", body.get("status") == "ok", body.get("status"))

# Verify POST /api/run endpoint is removed from server (cannot compile via server)
resp_run = client.post("/api/run", json={"code": "int main(){ return 0; }", "language": "c"})
check("POST /api/run endpoint removed (404)", resp_run.status_code == 404, resp_run.status_code)


def run_sandbox(code: str, language: str = "c", stdin: str = "", timeout: float = 5.0, **kwargs) -> dict:
    """Direct sandbox compilation and execution runner (preserved for AI grading & review)."""
    return compiler.run_code(code, language, stdin, timeout, **kwargs).to_dict()


# --- success (C) ---------------------------------------------------------- #
c_sum = """
#include <stdio.h>
int main(void) { int a, b; if (scanf("%d %d", &a, &b) != 2) return 1;
printf("%d\\n", a + b); return 0; }
"""
r = run_sandbox(c_sum, "c", "2 3")
check("c success", r["status"] == "success", r)
check("c stdout 5", r["stdout"].strip() == "5", r["stdout"])
check("c exit 0", r["exit_code"] == 0, r["exit_code"])
check("c timing measured", 0 < r["execution_time_ms"] < 1000, r["execution_time_ms"])

# --- success (C++) -------------------------------------------------------- #
cpp_code = """
#include <bits/stdc++.h>
int main() { std::vector<int> v; long long s = 0; int x;
while (std::cin >> x) { v.push_back(x); s += x; }
std::cout << s << " n=" << v.size() << std::endl; }
"""
r = run_sandbox(cpp_code, "cpp", "1 2 3 4 5\n")
check("cpp success", r["status"] == "success", r)
check("cpp stdout", r["stdout"].strip() == "15 n=5", r["stdout"])

# --- compile error -------------------------------------------------------- #
bad = "#include <stdio.h>\nint main(void){ printf(\"%d\\n\", undefined_thing) }"
r = run_sandbox(bad, "c", "")
check("compile_error status", r["status"] == "compile_error", r["status"])
check("compile_error output", "undefined_thing" in r["compile_output"], r["compile_output"][:120])
check("compile_error no exec time", r["execution_time_ms"] == 0.0, r["execution_time_ms"])

cpp_bad = "int main(){ int x = \"oops\"; return 0; }"
r = run_sandbox(cpp_bad, "cpp")
check("cpp compile_error", r["status"] == "compile_error", r["status"])

# --- time limit ----------------------------------------------------------- #
spin = "#include <unistd.h>\nint main(){ for(;;) usleep(1000); return 0; }"
r = run_sandbox(spin, "c", "", 3.0)
check("wall-clock TLE", r["status"] == "time_limit_exceeded", r["status"])
check("TLE ~timeout", 2.9 <= r["execution_time_ms"] / 1000.0 <= 5.0, r["execution_time_ms"])

busy = "int main(){ volatile double x = 0; for(;;) x += 1.0; return 0; }"
r = run_sandbox(busy, "c", "", 3.0)
check("CPU-burn TLE", r["status"] == "time_limit_exceeded", r["status"])

# Safe multi-process spawn test (ensures process group kill works without exhausting OS PID limits)
fork_bomb = """
#include <unistd.h>
int main(){ for (int i = 0; i < 3; i++) { fork(); } for(;;); return 0; }
"""
r = run_sandbox(fork_bomb, "c", "", 3.0)
check("fork+spin killed", r["status"] in ("time_limit_exceeded", "runtime_error"), r["status"])
_ps = __import__("subprocess").run(["ps", "-eo", "stat,comm"], capture_output=True, text=True).stdout.splitlines()
_zombies = [ln for ln in _ps if ln.split() and ln.split()[-1] == "solution" and ln.split()[0].startswith("Z")]
check("no zombies left", not _zombies, _zombies[:3])

# --- runtime error: segfault --------------------------------------------- #
segv = "#include <stdlib.h>\nint main(){ int *p = (int*)0; *p = 42; return 0; }"
r = run_sandbox(segv, "c")
check("segv runtime_error", r["status"] == "runtime_error", r["status"])
check("segv signal name", r["signal_name"] == "SIGSEGV", r["signal_name"])
check("segv signal detail in stderr", "SIGSEGV" in r["stderr"], r["stderr"][-140:])

# --- runtime error: memory ------------------------------------------------ #
alloc = """
#include <stdlib.h>
#include <string.h>
int main(){ char *p = malloc(1024u*1024u*64u); if(!p) return 2;
memset(p, 1, 1024u*1024u*64u); char *q = malloc(1024u*1024u*64u); if(!q) return 2;
memset(q, 2, 1024u*1024u*64u); char *z = malloc(1024u*1024u*256u);
return z ? (int)(((volatile char*)p)[0] + ((volatile char*)q)[0] + ((volatile char*)z)[0]) : 3; }
"""
r = run_sandbox(alloc, "c")
check("oom runtime_error", r["status"] == "runtime_error", (r["status"], r["exit_code"], r["stderr"][-160:]))

cpp_oom = """
#include <new>
#include <vector>
int main(){ std::vector<char> v; try { for(;;) v.resize(v.size() + (1<<20)); }
catch (const std::bad_alloc&) { throw; } return 0; }
"""
r = run_sandbox(cpp_oom, "cpp")
check("cpp bad_alloc runtime_error", r["status"] == "runtime_error", (r["status"], r["stderr"][-200:]))
check("cpp bad_alloc mentions memory", "memory" in r["stderr"].lower(), r["stderr"][-200:])

# --- per-problem memory budget (``Problem.memory_limit_mb``) --------------- #
# A teacher's per-problem budget has to reach ``RLIMIT_AS``. Whether the kernel
# then *refuses* an allocation is host-dependent (some kernels do not enforce
# ``RLIMIT_AS`` at all), so the probe reports the ceiling it was given instead of
# depending on an allocation failing.
limits_probe = r"""
#include <stdio.h>
#include <sys/resource.h>
int main(void){ struct rlimit rl; getrlimit(RLIMIT_AS, &rl);
printf("%llu\n", (unsigned long long)(rl.rlim_cur / (1024u*1024u))); return 0; }
"""
mb = compiler.MEMORY_LIMIT_BYTES // (1024 * 1024)
result = compiler.run_code(limits_probe, "c", "", 5.0)
check("run_code default keeps CODE_MEMORY_LIMIT_MB",
      result.stdout.strip() == str(mb), (result.stdout.strip(), mb))
result = compiler.run_code(limits_probe, "c", "", 5.0, memory_limit_mb=64)
check("run_code applies memory_limit_mb", result.stdout.strip() == "64", result.stdout.strip())
batch = compiler.run_code_batch(limits_probe, "c", ["", ""], 5.0, memory_limit_mb=48)
check("run_code_batch applies memory_limit_mb",
      [row.stdout.strip() for row in batch] == ["48", "48"],
      [row.stdout.strip() for row in batch])
check("compile phase stays exempt from the memory cap",
      compiler.run_code(limits_probe, "c", "", 5.0, memory_limit_mb=16).status == "success")
check("memory override clamps to the floor",
      compiler._resolve_memory_limit_bytes(1) == compiler.MEMORY_LIMIT_FLOOR_MB * 1024 * 1024,
      compiler._resolve_memory_limit_bytes(1) // 1024 ** 2)
check("memory override clamps to the ceiling",
      compiler._resolve_memory_limit_bytes(10 ** 6) == compiler.MEMORY_LIMIT_CEIL_MB * 1024 * 1024,
      compiler._resolve_memory_limit_bytes(10 ** 6) // 1024 ** 2)
check("a 0 or unusable override falls back to the default",
      compiler._resolve_memory_limit_bytes(0) == compiler.MEMORY_LIMIT_BYTES
      and compiler._resolve_memory_limit_bytes("nope") == compiler.MEMORY_LIMIT_BYTES)

# The verdict must name the budget the student actually ran against.
bad_alloc_note = (
    "#include <stdio.h>\nint main(){ fprintf(stderr, \"std::bad_alloc\\n\"); return 1; }"
)
result = compiler.run_code(bad_alloc_note, "c", "", 5.0, memory_limit_mb=32)
check("memory verdict quotes the active per-problem limit",
      "memory limit of 32 MB" in result.stderr, result.stderr[-140:])
result = compiler.run_code(bad_alloc_note, "c", "", 5.0)
check("memory verdict falls back to the global limit",
      f"memory limit of {mb} MB" in result.stderr, result.stderr[-140:])

# --- nonzero exit --------------------------------------------------------- #
r = run_sandbox("int main(){ return 7; }", "c")
check("nonzero exit -> runtime_error", r["status"] == "runtime_error" and r["exit_code"] == 7, r)

# --- output volume / no stdin --------------------------------------------- #
spam = "#include <stdio.h>\nint main(){ for(long i=0;i<400000;i++) puts(\"01234567890123456789\"); return 0; }"
r = run_sandbox(spam, "c")
check("output truncated", r["status"] == "success" and r["truncated"], (len(r["stdout"]), r["truncated"]))

r = run_sandbox("#include <stdio.h>\n#include <stdlib.h>\nint main(){ puts(getenv(\"PATH\")?\"env-ok\":\"no-env\"); }", "c")
check("runs with stdin omitted", r["status"] == "success" and "env-ok" in r["stdout"], r)

# --- detached descendant must never hang the judge ------------------------ #
# A submission that forks, lets the parent exit and keeps the pipes open in the
# child used to block ``_execute`` forever in ``stream.close()``: the judge thread
# never returned and its MAX_CONCURRENT_RUNS slot was leaked permanently.
orphan_src = """
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    if (fork() == 0) { sleep(120); _exit(0); }   /* inherits stdout */
    puts("parent done");
    return 0;
}
"""
_t0 = time.perf_counter()
r = run_sandbox(orphan_src, "c", "x" * 190_000, 10.0)
_elapsed = time.perf_counter() - _t0
check(
    "orphan holding stdout does not hang the judge",
    r["status"] == "success" and "parent done" in r["stdout"] and _elapsed < 12.0,
    (r["status"], f"{_elapsed:.1f}s"),
)

# A program that never reads a large stdin used to pin the writer thread, and the
# caller then blocked forever closing that same pipe. 190_000 bytes is more than
# one pipe buffer, so the write really does block.
_t0 = time.perf_counter()
r = run_sandbox("int main(){ return 0; }", "c", "x" * 190_000, 10.0)
_elapsed = time.perf_counter() - _t0
check(
    "unconsumed huge stdin returns promptly",
    r["status"] == "success" and _elapsed < 12.0,
    (r["status"], f"{_elapsed:.1f}s"),
)

# --- fork bomb is capped by RLIMIT_NPROC (AGENTS.md rule) ----------------- #
# 12 forks would be 4096 processes. The task limit must make fork() fail instead
# of exhausting the host. Deliberately small - see AGENTS.md safety rules.
fork_src = """
#include <stdio.h>
#include <unistd.h>
int main(void) {
    int failed = 0;
    for (int i = 0; i < 12; i++) { if (fork() < 0) failed++; }
    printf("failures=%d\\n", failed);
    return 0;
}
"""
r = run_sandbox(fork_src, "c")
_created = r["stdout"].count("failures=")
check(
    "fork bomb cannot outgrow the task limit",
    r["status"] == "success" and 0 < _created < 4096,
    f"processes printed: {_created}",
)

# --- validation ----------------------------------------------------------- #
check("bad language rejected safely", compiler.run_code("int main(){}", "python", "", 5.0).status == "internal_error")

# --- concurrency / leak check --------------------------------------------- #
import concurrent.futures as cf


def one(i: int) -> str:
    return run_sandbox("#include <unistd.h>\nint main(){ usleep(300000); return 0; }", "c")["status"]


with cf.ThreadPoolExecutor(max_workers=8) as pool:
    statuses = list(pool.map(one, range(8)))
check("8 concurrent runs all success", all(s == "success" for s in statuses), set(statuses))

import os, glob, subprocess

leftovers = glob.glob(os.path.join("/tmp", "cj-*"))
check("temp dirs cleaned", not leftovers, leftovers[:3])
procs = subprocess.run(["pgrep", "-fc", "^solution$"], capture_output=True, text=True)
check("no stray binaries running", procs.returncode != 0 or procs.stdout.strip() == "0", procs.stdout.strip())

print()
print("FAILED:", failures if failures else "none")
sys.exit(1 if failures else 0)
