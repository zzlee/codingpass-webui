"""End-to-end tests for the Phase 2 API (no pytest needed: ``python test_api.py``).

Runs against a throwaway SQLite database in ``/tmp`` so the real
``codingpass.db`` is never touched. Covers authentication and role guards,
classroom enrolment, problem visibility, output-based grading with hidden test
cases, and the submission history views.

``test_judge.py`` remains the authority on sandbox behaviour; this file only
checks that grading is orchestrated and reported correctly.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

# --- environment must be configured before main/auth import ---------------- #
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-test-", suffix=".db")
os.close(_DB_FD)
os.unlink(_DB_PATH)  # let SQLAlchemy create the file (and its WAL siblings)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "integration-test-secret-0123456789-abcdefghij"
# Cost 10 keeps the suite quick while still exercising the real hashing path.
os.environ["BCRYPT_ROUNDS"] = "10"
os.environ.setdefault("CODE_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient  # noqa: E402

import auth as auth_module  # noqa: E402
import main  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import User  # noqa: E402

client = TestClient(main.app)
client.__enter__()  # run lifespan: creates tables + seeds the demo dataset
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def login(username: str, password: str) -> str:
    """Return a bearer token, failing the suite if the login does not work."""
    reply = client.post("/api/auth/login", json={"username": username, "password": password})
    if reply.status_code != 200:
        check(f"login {username} (fixture)", False, reply.text[:200])
        raise SystemExit(1)
    return reply.json()["access_token"]


def call(method: str, path: str, token: str | None = None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return getattr(client, method)(path, headers=headers, **kwargs)


ADMIN = login("admin", "admin123")
TEACHER = login("teacher1", "teacher123")
ALICE = login("student1", "student123")
BOB = login("student2", "student123")

# --- seed integrity --------------------------------------------------------- #
r = call("get", "/api/auth/me", ADMIN)
check("seed admin login", r.status_code == 200 and r.json()["role"] == "admin", r.text[:200])
check(
    "timestamps serialise as UTC",
    r.json()["created_at"].endswith("+00:00"),
    r.json()["created_at"],
)
r = call("get", "/api/auth/me", ALICE)
check("seed student login", r.json()["full_name"] == "Alice", r.text[:200])
r = client.post("/api/auth/login", json={"username": "student1@codingpass.local", "password": "student123"})
check("login by email works", r.status_code == 200, r.status_code)

# --- auth guardrails -------------------------------------------------------- #
r = client.get("/api/auth/me")
check("me without token -> 401", r.status_code == 401, r.status_code)
r = call("get", "/api/auth/me", "not-a-jwt-at-all")
check("me with garbage token -> 401", r.status_code == 401, r.text[:120])
r = client.post("/api/auth/login", json={"username": "student1", "password": "wrong-pass"})
check("wrong password -> 401", r.status_code == 401, r.status_code)
r = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever1"})
check("unknown account -> 401 (same shape)", r.status_code == 401 and "detail" in r.json(), r.text[:120])

expired_token_pair = None
with SessionLocal() as session:
    alice_row = session.query(User).filter(User.username == "student1").one()
    stale_token, _ = auth_module.create_access_token(alice_row, ttl_seconds=-120)
r = call("get", "/api/auth/me", stale_token)
check("expired token -> 401 with hint", r.status_code == 401 and "expired" in r.text.lower(), r.text[:160])

r = client.post(
    "/api/auth/register",
    json={
        "username": "carol",
        "email": "carol@example.com",
        "password": "carol1234",
        "full_name": "Carol Lin",
    },
)
check(
    "register student -> 201 student role",
    r.status_code == 201 and r.json()["role"] == "student",
    r.text[:200],
)
CAROL = login("carol", "carol1234")
r = client.post(
    "/api/auth/register",
    json={"username": "carol", "email": "other@example.com", "password": "carol1234"},
)
check("duplicate username -> 409", r.status_code == 409, r.status_code)
r = client.post(
    "/api/auth/register",
    json={"username": "dave", "email": "carol@example.com", "password": "carol1234"},
)
check("duplicate email -> 409", r.status_code == 409, r.status_code)
r = client.post(
    "/api/auth/register",
    json={"username": "9bad!", "email": "dave@example.com", "password": "carol1234"},
)
check("invalid username -> 422", r.status_code == 422, r.status_code)
r = client.post(
    "/api/auth/register",
    json={"username": "dave", "email": "not-an-email", "password": "carol1234"},
)
check("invalid email -> 422", r.status_code == 422, r.status_code)
r = client.post(
    "/api/auth/register",
    json={"username": "dave", "email": "dave@example.com", "password": "short"},
)
check("short password -> 422", r.status_code == 422, r.status_code)
r = client.post(
    "/api/auth/register",
    json={"username": "evil", "email": "evil@example.com", "password": "evil12345", "role": "admin"},
)
check("anonymous cannot self-assign admin -> 403", r.status_code == 403, r.text[:160])
r = call(
    "post",
    "/api/auth/register",
    TEACHER,
    json={"username": "sneaky", "email": "sneaky@example.com", "password": "sneaky123", "role": "admin"},
)
check("teacher cannot self-assign admin -> 403", r.status_code == 403, r.status_code)
r = call(
    "post",
    "/api/auth/register",
    ADMIN,
    json={"username": "teacher2", "email": "teacher2@codingpass.local", "password": "teacher123",
          "full_name": "Dr. Wu", "role": "teacher"},
)
check("admin can create a teacher -> 201", r.status_code == 201 and r.json()["role"] == "teacher", r.text[:200])
TEACHER2 = login("teacher2", "teacher123")

# --- classrooms ------------------------------------------------------------- #
r = call("get", "/api/classrooms", ALICE)
rows = r.json()
check("student sees only joined classes", r.status_code == 200 and len(rows) == 1, rows)
check("CS101 roster and problem counts", rows and rows[0]["code"] == "CS101" and rows[0]["student_count"] == 1
      and rows[0]["problem_count"] == 2, rows)
CLASS_ID = rows[0]["id"]

r = call("get", f"/api/classrooms/{CLASS_ID}", ALICE)
check("class detail roster contains Alice", any(s["username"] == "student1" for s in r.json()["students"]), r.text[:250])
check("class detail lists 2 problems", len(r.json()["problems"]) == 2, r.text[:120])

r = call("get", f"/api/classrooms/{CLASS_ID}", CAROL)
check("outsider cannot read a class -> 403", r.status_code == 403, r.status_code)
r = call("post", "/api/classrooms/join", CAROL, json={"code": "NOPE1"})
check("join unknown code -> 404", r.status_code == 404, r.text[:120])
r = call("post", "/api/classrooms/join", CAROL, json={"code": "cs101"})
check("join is case-insensitive -> 200", r.status_code == 200, r.text[:200])
r = call("post", "/api/classrooms/join", CAROL, json={"code": "CS101"})
check("rejoin same class -> 409", r.status_code == 409, r.text[:120])
r = call("post", "/api/classrooms/join", TEACHER, json={"code": "CS101"})
check("teacher cannot join a class -> 403", r.status_code == 403, r.text[:160])
r = call("post", "/api/classrooms", ALICE, json={"name": "Student Owned"})
check("student cannot create a class -> 403", r.status_code == 403, r.text[:160])
r = call("post", "/api/classrooms", TEACHER, json={"name": "Data Structures", "description": "DS"})
check("teacher creates class -> 201", r.status_code == 201, r.text[:200])
generated = r.json()
check("generated invite code shape",
      len(generated["code"]) == main.INVITE_CODE_LENGTH and generated["code"].isalnum()
      and generated["teacher_name"] == "Prof. Chen", generated)
NEW_CLASS = generated["id"]
r = call("post", "/api/classrooms", TEACHER, json={"name": "Clash", "code": "cs101"})
check("duplicate invite code -> 409", r.status_code == 409, r.text[:160])
r = call("post", "/api/classrooms", ADMIN, json={"name": "Another", "code": "bad code!"})
check("invalid invite code -> 422", r.status_code == 422, r.status_code)
r = call("post", "/api/classrooms", ADMIN, json={"name": "Admin Owned", "teacher_id": 999999})
check("assign to unknown teacher -> 404", r.status_code == 404, r.text[:160])
r = call("get", "/api/classrooms", ADMIN)
check("admin sees every class", len(r.json()) == 2, [c["code"] for c in r.json()])
r = call("get", "/api/classrooms", TEACHER)
check("teacher sees own classes only", {c["code"] for c in r.json()} == {"CS101", generated["code"]},
      [c["code"] for c in r.json()])

# --- problem bank ----------------------------------------------------------- #
r = call("get", "/api/problems", ALICE)
payload = r.json()
titles = {p["title"] for p in payload["problems"]}
check("student sees the 2 seeded problems", payload["total"] == 2 and len(titles) == 2, titles)
check("seeded sample/hidden split is correct",
      sorted((p["sample_count"], p["hidden_count"]) for p in payload["problems"]) == [(2, 5), (2, 9)],
      [(p["sample_count"], p["hidden_count"]) for p in payload["problems"]])
TWO_SUM = next(p["id"] for p in payload["problems"] if p["title"].startswith("Two Sum"))
PRIME = next(p["id"] for p in payload["problems"] if p["title"].startswith("Check Prime"))

r = call("get", f"/api/problems/{TWO_SUM}", ALICE)
detail = r.json()
check("problem detail: samples only", detail["test_cases"] and all(c["is_sample"] for c in detail["test_cases"])
      and len(detail["test_cases"]) == 2, [c["is_sample"] for c in detail["test_cases"]])
check("problem detail: hidden count only, not data",
      detail["hidden_count"] == 5 and all(c["expected_output"] for c in detail["test_cases"]), detail["hidden_count"])
check("problem detail ships starter code", 'include <stdio.h>' in detail["starter_code_c"]
      and 'include <iostream>' in detail["starter_code_cpp"], detail["starter_code_c"][:40])
check("problem detail exposes limits",
      detail["time_limit_sec"] == 3.0 and detail["memory_limit_mb"] == 256, (detail["time_limit_sec"], detail["memory_limit_mb"]))

r = call("get", "/api/problems/999999", ALICE)
check("unknown problem -> 404", r.status_code == 404, r.status_code)

r = call("post", "/api/problems", TEACHER, json={
    "title": "Private Draft", "description": "not published yet",
    "test_cases": [{"input": "1\n", "expected_output": "1\n", "is_sample": True}],
})
check("teacher creates a private problem -> 201", r.status_code == 201, r.text[:200])
PRIVATE = r.json()["id"]
check("teacher sees their own cases with full data",
      len(r.json()["test_cases"]) == 1 and r.json()["test_cases"][0]["expected_output"] == "1\n", r.text[:200])
r = call("get", f"/api/problems/{PRIVATE}", ALICE)
check("student cannot open a private problem -> 403", r.status_code == 403, r.text[:160])
r = call("get", "/api/problems", ALICE)
check("private problem absent from student list",
      all(p["id"] != PRIVATE for p in r.json()["problems"]), [p["id"] for p in r.json()["problems"]])
r = call("get", "/api/problems?mine=true", TEACHER2)
check("other teacher sees no problems of peers", r.json()["total"] == 0, r.text[:160])

r = call("post", f"/api/problems/{PRIVATE}/testcases", TEACHER2,
         json={"input": "2\n", "expected_output": "2\n", "is_sample": False})
check("peer teacher cannot edit test cases -> 403", r.status_code == 403, r.text[:160])
r = call("post", f"/api/problems/{PRIVATE}/testcases", TEACHER,
         json={"input": "2\n", "expected_output": "2\n", "is_sample": False})
appended = r.json()
check("owner can append a test case -> 201", r.status_code == 201 and appended["hidden_count"] == 1, r.text[:200])
check("owner sees the appended hidden case",
      any(not c["is_sample"] and c["expected_output"] == "2\n" for c in appended["test_cases"]),
      [c["is_sample"] for c in appended["test_cases"]])

# --- grading ---------------------------------------------------------------- #
CORRECT_C = """
#include <stdio.h>
int main(void) { long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) return 1;
    printf("%lld\\n", a + b); return 0; }
"""
OFF_BY_ONE_C = """
#include <stdio.h>
int main(void) { long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) return 1;
    printf("%lld\\n", a + b + 1); return 0; }
"""
BROKEN_C = "int main(void){ return undefined_symbol_here; }"
SPIN_C = "int main(void){ for (;;); return 0; }"
OOM_C = """
#include <stdlib.h>
#include <string.h>
int main(void){ char *p = malloc(300u*1024u*1024u);
  if (!p) return 3; memset(p, 1, 300u*1024u*1024u);
  return ((volatile char *)p)[0] == 99 ? 0 : 4; }
"""
#: Reports a memory failure and exits non-zero. The judge then appends the budget
#: that was actually in force, which is how the suite proves a problem's own
#: ``memory_limit_mb`` - not ``CODE_MEMORY_LIMIT_MB`` - reached the sandbox.
MEM_LIMIT_PROBE_C = """
#include <stdio.h>
int main(void){
  fprintf(stderr, "terminate called after throwing an instance of 'std::bad_alloc'\\n");
  return 3; }
"""
PRIME_CPP = """
#include <iostream>
using namespace std;
bool prime(long long n){ if (n < 2) return false; if (n % 2 == 0) return n == 2;
  for (long long d = 3; d * d <= n; d += 2) if (n % d == 0) return false; return true; }
int main(){ long long n; if (!(cin >> n)) return 1; cout << (prime(n) ? "Prime" : "Not Prime") << endl; }
"""

r = call("post", f"/api/problems/{TWO_SUM}/submit", ALICE, json={"language": "c", "code": CORRECT_C})
report = r.json()
check("accepted submission", r.status_code == 200 and report["status"] == "accepted", r.text[:250])
check("score 100 and full pass", report["score"] == 100.0 and report["passed_count"] == 7 and report["total_count"] == 7, report)
check("submission row created", isinstance(report["submission_id"], int), report["submission_id"])
check("hidden summary reported", report["hidden_summary"] == {"total": 5, "passed": 5, "failed": 0}, report["hidden_summary"])
samples = [c for c in report["results"] if c["is_sample"]]
check("sample verdicts carry output", len(samples) == 2 and samples[0]["stdout"].strip() == "5", samples[0])
hidden = [c for c in report["results"] if not c["is_sample"]]
check("hidden cases redacted", all(c["stdout"] is None and c["expected_output"] is None and c["input"] is None
                                   and c["passed"] for c in hidden), hidden[:1])
check("per-case timings measured", all(c["execution_time_ms"] > 0 for c in report["results"]),
      [c["execution_time_ms"] for c in report["results"]])
check("compile time reported even without warnings", report["compile_time_ms"] > 0, report["compile_time_ms"])
SUBMISSION_ID = report["submission_id"]

r = call("post", f"/api/problems/{TWO_SUM}/submit", ALICE, json={"language": "c", "code": OFF_BY_ONE_C})
report = r.json()
check("wrong answer verdict", report["status"] == "wrong_answer" and report["score"] == 0.0
      and report["passed_count"] == 0, report)
wrong_samples = [c for c in report["results"] if c["is_sample"]]
check("sample verdicts expose the expected vs actual output",
      len(wrong_samples) == 2 and wrong_samples[0]["stdout"].strip() == "6"
      and wrong_samples[0]["expected_output"].strip() == "5" and not wrong_samples[0]["passed"],
      wrong_samples[0])

r = call("post", f"/api/problems/{TWO_SUM}/submit", ALICE, json={"language": "c", "code": BROKEN_C})
report = r.json()
check("compile error short-circuits", report["status"] == "compile_error" and report["results"] == []
      and report["passed_count"] == 0 and report["total_count"] == 7, report["status"])
check("compiler diagnostics returned", "error" in report["compile_output"].lower(), report["compile_output"][:120])

r = call("post", f"/api/problems/{PRIME}/submit", ALICE, json={"language": "cpp", "code": PRIME_CPP})
report = r.json()
check("C++ prime solution accepted", report["status"] == "accepted" and report["total_count"] == 11, report)

# Time limits and crashes need a small problem set to keep the suite quick.
r = call("post", "/api/problems", TEACHER, json={
    "title": "Tiny Limits Harness", "difficulty": "easy", "classroom_id": CLASS_ID,
    "time_limit_sec": 1.0, "memory_limit_mb": 64,
    "test_cases": [{"input": "2 3\n", "expected_output": "5\n", "is_sample": True},
                   {"input": "4 4\n", "expected_output": "8\n", "is_sample": False}],
})
HARNESS = r.json()["id"]
check("teacher creates a class problem -> 201", r.status_code == 201, r.text[:200])
r = call("post", f"/api/problems/{HARNESS}/submit", ALICE, json={"language": "c", "code": SPIN_C})
report = r.json()
check("infinite loop -> time_limit_exceeded", report["status"] == "time_limit_exceeded", report)
check("TLE is recorded on the submission row", report["passed_count"] == 0 and report["total_count"] == 2, report)

r = call("post", f"/api/problems/{HARNESS}/submit", ALICE, json={"language": "c", "code": OOM_C})
check("over-limit allocation -> runtime_error", r.json()["status"] == "runtime_error", r.json())

# A teacher trial run keeps the submission counts below untouched while proving the
# problem's own 64 MB budget - not the server's default - was the one enforced.
r = call("post", f"/api/problems/{HARNESS}/submit", TEACHER, json={"language": "c", "code": MEM_LIMIT_PROBE_C})
report = r.json()
check("problem memory_limit_mb reaches the sandbox",
      report["status"] == "runtime_error"
      and "memory limit of 64 MB" in (report["results"][0]["stderr"] or ""),
      report["results"][0]["stderr"])
r = call("post", f"/api/problems/{TWO_SUM}/submit", TEACHER, json={"language": "c", "code": MEM_LIMIT_PROBE_C})
report = r.json()
check("a 256 MB problem quotes 256 MB",
      "memory limit of 256 MB" in (report["results"][0]["stderr"] or ""),
      report["results"][0]["stderr"])

r = call("post", f"/api/problems/{HARNESS}/submit", TEACHER, json={"language": "c", "code": CORRECT_C})
report = r.json()
check("teacher run is a trial run (not stored)", report["trial_run"] and report["submission_id"] is None, report)
check("teacher sees hidden case output",
      all(c["stdout"] is not None for c in report["results"] if not c["is_sample"]), report["results"][-1])

r = call("post", "/api/problems", TEACHER, json={"title": "No Test Cases Yet", "classroom_id": CLASS_ID})
EMPTY = r.json()["id"]
r = call("post", f"/api/problems/{EMPTY}/submit", ALICE, json={"language": "c", "code": CORRECT_C})
check("problem without test cases -> 409", r.status_code == 409, r.text[:160])

r = call("post", f"/api/problems/{PRIVATE}/submit", ALICE, json={"language": "c", "code": CORRECT_C})
check("cannot submit to an invisible problem -> 403", r.status_code == 403, r.text[:160])
r = call("post", f"/api/problems/{TWO_SUM}/submit", ALICE, json={"language": "python", "code": CORRECT_C})
check("unsupported language -> 422", r.status_code == 422, r.status_code)

# --- submission history ----------------------------------------------------- #
r = call("get", "/api/submissions", ALICE)
payload = r.json()
check("student sees own history", payload["total"] == 6 and all(s["student_username"] == "student1" for s in payload["submissions"]), payload["total"])
check("history rows carry problem titles", {s["problem_title"] for s in payload["submissions"]} >=
      {"Two Sum (A + B Problem)", "Tiny Limits Harness"}, [s["problem_title"] for s in payload["submissions"]])
r = call("get", "/api/submissions?status=accepted", ALICE)
check("status filter works", r.json()["total"] == 2, r.json()["total"])
r = call("get", "/api/submissions?limit=1", ALICE)
check("pagination respected", r.json()["count"] == 1 and r.json()["total"] == 6, r.json()["count"])

r = call("get", "/api/submissions", BOB)
check("other student has empty history", r.json()["total"] == 0, r.json()["total"])

r = call("get", f"/api/submissions?classroom_id={CLASS_ID}", TEACHER)
rows = r.json()
check("teacher sees class submissions", rows["total"] == 6 and
      {s["student_username"] for s in rows["submissions"]} == {"student1"}, rows["total"])
r = call("get", f"/api/submissions?classroom_id={NEW_CLASS}", TEACHER)
check("teacher sees empty set for a new class", r.json()["total"] == 0, r.json())
r = call("get", "/api/submissions?student_id=1", TEACHER)
check("teacher cannot query an unrelated student -> 403", r.status_code == 403, r.text[:160])
r = call("get", "/api/submissions?student_id=3", TEACHER)
check("teacher can query an enrolled student", r.json()["total"] == 6, r.text[:160])
r = call("get", "/api/submissions?student_id=3", BOB)
check("student cannot peek at others -> 403", r.status_code == 403, r.text[:160])
r = call("get", "/api/submissions?classroom_id=999", BOB)
check("student cannot filter by a foreign class -> 403", r.status_code == 403, r.text[:160])

r = call("get", f"/api/submissions/{SUBMISSION_ID}", ALICE)
check("author can read their source back", r.status_code == 200 and "scanf" in r.json()["code"], r.text[:120])
r = call("get", f"/api/submissions/{SUBMISSION_ID}", TEACHER)
check("problem teacher can review the source", r.status_code == 200, r.status_code)
r = call("get", f"/api/submissions/{SUBMISSION_ID}", BOB)
check("classmate cannot read the source -> 403", r.status_code == 403, r.text[:160])
r = call("get", "/api/submissions/424242", ADMIN)
check("unknown submission -> 404", r.status_code == 404, r.status_code)

# --- phase 1 endpoints still intact ----------------------------------------- #
r = client.get("/api/health")
check("GET /api/health still ok", r.status_code == 200 and r.json()["status"] == "ok", r.text[:120])
r = client.post("/api/run", json={
    "code": "#include <stdio.h>\nint main(){printf(\"%d\\n\",6*7);return 0;}",
    "language": "c", "stdin": ""})
check("POST /api/run is removed (404)", r.status_code == 404, r.status_code)
r = client.get("/api/config")
check("GET /api/config advertises roles", "teacher" in r.json()["roles"], r.json().get("roles"))
leftovers = list(pathlib.Path("/tmp").glob("cj-*"))
check("sandbox temp dirs cleaned", not leftovers, [str(p) for p in leftovers][:3])

client.__exit__(None, None, None)
print()
print("FAILED:", failures if failures else "none")
print(f"({len(failures)} failed of the checks printed above; db kept at {_DB_PATH})")
sys.exit(1 if failures else 0)
