"""Smoke tests for the judge backend (no pytest needed: `python test_judge.py`)."""

from __future__ import annotations

import json
import sys

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

# --- success (C) ---------------------------------------------------------- #
c_sum = """
#include <stdio.h>
int main(void) { int a, b; if (scanf("%d %d", &a, &b) != 2) return 1;
printf("%d\\n", a + b); return 0; }
"""
r = client.post("/api/run", json={"code": c_sum, "language": "c", "stdin": "2 3"}).json()
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
r = client.post("/api/run", json={"code": cpp_code, "language": "cpp", "stdin": "1 2 3 4 5\n"}).json()
check("cpp success", r["status"] == "success", r)
check("cpp stdout", r["stdout"].strip() == "15 n=5", r["stdout"])

# --- compile error -------------------------------------------------------- #
bad = "#include <stdio.h>\nint main(void){ printf(\"%d\\n\", undefined_thing) }"
r = client.post("/api/run", json={"code": bad, "language": "c", "stdin": ""}).json()
check("compile_error status", r["status"] == "compile_error", r["status"])
check("compile_error output", "undefined_thing" in r["compile_output"], r["compile_output"][:120])
check("compile_error no exec time", r["execution_time_ms"] == 0.0, r["execution_time_ms"])

cpp_bad = "int main(){ int x = \"oops\"; return 0; }"
r = client.post("/api/run", json={"code": cpp_bad, "language": "cpp"}).json()
check("cpp compile_error", r["status"] == "compile_error", r["status"])

# --- time limit ----------------------------------------------------------- #
spin = "#include <unistd.h>\nint main(){ for(;;) usleep(1000); return 0; }"
r = client.post("/api/run", json={"code": spin, "language": "c"}).json()
check("wall-clock TLE", r["status"] == "time_limit_exceeded", r["status"])
check("TLE ~timeout", 2.9 <= r["execution_time_ms"] / 1000.0 <= 5.0, r["execution_time_ms"])

busy = "int main(){ volatile double x = 0; for(;;) x += 1.0; return 0; }"
r = client.post("/api/run", json={"code": busy, "language": "c"}).json()
check("CPU-burn TLE", r["status"] == "time_limit_exceeded", r["status"])

# Safe multi-process spawn test (ensures process group kill works without exhausting OS PID limits)
fork_bomb = """
#include <unistd.h>
int main(){ for (int i = 0; i < 3; i++) { fork(); } for(;;); return 0; }
"""
r = client.post("/api/run", json={"code": fork_bomb, "language": "c"}).json()
check("fork+spin killed", r["status"] in ("time_limit_exceeded", "runtime_error"), r["status"])
_ps = __import__("subprocess").run(["ps", "-eo", "stat,comm"], capture_output=True, text=True).stdout.splitlines()
_zombies = [ln for ln in _ps if ln.split() and ln.split()[-1] == "solution" and ln.split()[0].startswith("Z")]
check("no zombies left", not _zombies, _zombies[:3])

# --- runtime error: segfault --------------------------------------------- #
segv = "#include <stdlib.h>\nint main(){ int *p = (int*)0; *p = 42; return 0; }"
r = client.post("/api/run", json={"code": segv, "language": "c"}).json()
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
r = client.post("/api/run", json={"code": alloc, "language": "c"}).json()
check("oom runtime_error", r["status"] == "runtime_error", (r["status"], r["exit_code"], r["stderr"][-160:]))

cpp_oom = """
#include <new>
#include <vector>
int main(){ std::vector<char> v; try { for(;;) v.resize(v.size() + (1<<20)); }
catch (const std::bad_alloc&) { throw; } return 0; }
"""
r = client.post("/api/run", json={"code": cpp_oom, "language": "cpp"}).json()
check("cpp bad_alloc runtime_error", r["status"] == "runtime_error", (r["status"], r["stderr"][-200:]))
check("cpp bad_alloc mentions memory", "memory" in r["stderr"].lower(), r["stderr"][-200:])

# --- nonzero exit --------------------------------------------------------- #
r = client.post("/api/run", json={"code": "int main(){ return 7; }", "language": "c"}).json()
check("nonzero exit -> runtime_error", r["status"] == "runtime_error" and r["exit_code"] == 7, r)

# --- output volume / no stdin --------------------------------------------- #
spam = "#include <stdio.h>\nint main(){ for(long i=0;i<400000;i++) puts(\"01234567890123456789\"); return 0; }"
r = client.post("/api/run", json={"code": spam, "language": "c"}).json()
check("output truncated", r["status"] == "success" and r["truncated"], (len(r["stdout"]), r["truncated"]))

r = client.post("/api/run", json={"code": "#include <stdio.h>\n#include <stdlib.h>\nint main(){ puts(getenv(\"PATH\")?\"env-ok\":\"no-env\"); }", "language": "c"}).json()
check("runs with stdin omitted", r["status"] == "success" and "env-ok" in r["stdout"], r)

# --- validation ----------------------------------------------------------- #
r = client.post("/api/run", json={"code": "int main(){}", "language": "python"})
check("bad language 422", r.status_code == 422, r.status_code)
r = client.post("/api/run", json={"code": "int main(){}"})
check("missing language 422", r.status_code == 422, r.status_code)

# --- concurrency / leak check --------------------------------------------- #
import concurrent.futures as cf


def one(i: int) -> str:
    return client.post(
        "/api/run",
        json={"code": "#include <unistd.h>\nint main(){ usleep(300000); return 0; }", "language": "c"},
    ).json()["status"]


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
