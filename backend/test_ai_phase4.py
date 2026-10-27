"""End-to-end tests for the Phase 4 AI tutor / review API (no pytest needed: ``python test_ai_phase4.py``).

Runs against a throwaway SQLite database in ``/tmp`` so the real
``codingpass.db`` is never touched. Covers ``GET /api/ai/status``,
``POST /api/ai/analyze`` (playground, problem-scoped, authorisation, problem
visibility and hidden-test-case redaction), ``POST /api/submissions/{id}/ai-review``,
``PATCH /api/submissions/{id}/review`` and the in-place Phase 4 schema upgrade.

``test_judge.py`` stays the authority on sandbox behaviour and ``test_api.py`` on
grading; this file only checks that the AI layer is orchestrated, authorised,
redacted and persisted correctly.

**Determinism.** ``AI_FORCE_FALLBACK=1`` is pinned while assertions check concrete
numbers/strings, so those describe the deterministic heuristic reviewer
(:func:`ai_tutor._fallback_analyze`). The live vLLM path is exercised too, but
only when ``check_ai_status()`` reports it reachable, and then with shape-only
assertions - model prose is not byte-stable.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

# --- environment must be configured before main/ai_tutor import ------------- #
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-ai-test-", suffix=".db")
os.close(_DB_FD)
os.unlink(_DB_PATH)  # let SQLAlchemy create the file (and its WAL siblings)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "phase4-test-secret-0123456789-abcdefghijklmn"
# Cost 10 keeps the suite quick while still exercising the real hashing path.
os.environ["BCRYPT_ROUNDS"] = "10"
# Pinned for the numeric/string assertions; flipped to "0" for the live probe.
os.environ["AI_FORCE_FALLBACK"] = "1"
os.environ.setdefault("CODE_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import ai_tutor  # noqa: E402
import auth as auth_module  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
from database import SUBMISSION_MIGRATIONS, SessionLocal  # noqa: E402
from models import Submission, TestCase, User  # noqa: E402

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


def submission_row(submission_id: int) -> Submission:
    """Read the stored row straight from the database (bypasses the API)."""
    with SessionLocal() as session:
        return session.query(Submission).filter(Submission.id == submission_id).one()


#: Every field of ``AIAnalyzeResponse``.
ANALYSIS_KEYS = {
    "summary",
    "error_analysis",
    "time_complexity",
    "space_complexity",
    "suggestions",
    "suggested_score",
    "feedback_markdown",
    "model",
    "ai_powered",
    "detail",
}
#: The eight Phase 4 review columns, in response-field terms.
REVIEW_FIELDS = (
    "ai_feedback",
    "ai_suggested_score",
    "ai_model",
    "ai_reviewed_at",
    "teacher_feedback",
    "teacher_score",
    "reviewed_by_id",
    "reviewed_at",
)
#: The eight Stage 4 rubric/flag columns added on top of Phase 4. The five
#: subscores are serialised inside the nested ``rubric`` object rather than as
#: flat response fields, which is why the parity check below folds them in.
RUBRIC_FIELDS = (
    "compile_score",
    "sample_score",
    "random_score",
    "logic_score",
    "quality_score",
)
FLAG_FIELDS = ("presentation_warning", "wording_pass", "wording_violations")
#: Hidden inputs / expected outputs of the seeded Two Sum problem: none of them
#: may ever reach a caller through the AI layer.
HIDDEN_MARKERS = [
    "-2147483648 2147483647",
    "123456789 987654321",
    "1111111110",
    "100000 200000",
    "-10 25",
]


def leaks(*payloads: object) -> list[str]:
    """Return the hidden-case markers found anywhere in the given payloads."""
    blob = " ".join(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
        for payload in payloads
    )
    return [marker for marker in HIDDEN_MARKERS if marker in blob]


# --- fixtures --------------------------------------------------------------- #
ADMIN = login("admin", "admin123")
TEACHER = login("teacher1", "teacher123")
ALICE = login("student1", "student123")
BOB = login("student2", "student123")
ADMIN_ID = call("get", "/api/auth/me", ADMIN).json()["id"]
TEACHER_ID = call("get", "/api/auth/me", TEACHER).json()["id"]
ALICE_ID = call("get", "/api/auth/me", ALICE).json()["id"]
r = call(
    "post",
    "/api/auth/register",
    ADMIN,
    json={"username": "teacher2", "email": "teacher2@codingpass.local", "password": "teacher123",
          "full_name": "Dr. Wu", "role": "teacher"},
)
check("admin can create an unrelated teacher (fixture)", r.status_code == 201, r.text[:200])
TEACHER2 = login("teacher2", "teacher123")
TEACHER2_ID = call("get", "/api/auth/me", TEACHER2).json()["id"]
check("the peer teacher is a different account", TEACHER2_ID != TEACHER_ID, (TEACHER2_ID, TEACHER_ID))

r = call("get", "/api/problems", ALICE)
TWO_SUM = next(p["id"] for p in r.json()["problems"] if p["title"].startswith("Two Sum"))
check("seeded Two Sum problem resolved (fixture)", isinstance(TWO_SUM, int), TWO_SUM)
with SessionLocal() as session:
    hidden_inputs = [
        case.input
        for case in session.query(TestCase)
        .filter(TestCase.problem_id == TWO_SUM, TestCase.is_sample.is_(False))
        .all()
    ]
check("fixture really has hidden cases to leak", len(hidden_inputs) == 5, hidden_inputs)

# --- source fixtures -------------------------------------------------------- #
CORRECT_C = """
#include <stdio.h>
int main(void) { long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) return 1;
    printf("%lld\\n", a + b); return 0; }
"""
BROKEN_C = "int main(void){ return undefined_symbol_here; }"
# Quadratic on purpose - this is the TLE fixture. The outer loop is a ``while``
# because ``ai_tutor._NESTED_LOOP_RE`` cannot read across a ``for`` header (its
# ``[^{};]*`` stops at the header semicolons), so a ``for``/``for`` pair is
# profiled as O(N). That gap is pinned by the last check in this file.
NESTED_C = """
#include <stdio.h>
int main(void) {
    int n, i, j; long long sum = 0;
    if (scanf("%d", &n) != 1) return 1;
    i = 0;
    while (i < n) {
        j = 0;
        while (j < n) { sum += (long long)i * j; j = j + 1; }
        i = i + 1;
    }
    printf("%lld\\n", sum); return 0; }
"""
NESTED_CPP = """
#include <iostream>
using namespace std;
int main() { int n; long long sum = 0;
    if (!(cin >> n)) return 1;
    int i = 0;
    while (i < n) { int j = 0; while (j < n) { sum += (long long)i * j; ++j; } ++i; }
    cout << sum << endl; return 0; }
"""

# --- 1. GET /api/ai/status -------------------------------------------------- #
r = client.get("/api/ai/status")
check("GET /api/ai/status -> 200", r.status_code == 200, r.text[:160])
body = r.json()
check(
    "status payload has the five advertised keys",
    set(body) == {"available", "provider", "model", "base_url", "detail"},
    sorted(body),
)
check(
    "status echoes the configured provider/model/base_url",
    body["provider"] == ai_tutor.AI_PROVIDER
    and body["model"] == ai_tutor.VLLM_MODEL
    and body["base_url"] == ai_tutor.VLLM_BASE_URL,
    body,
)
check(
    "status types are JSON-safe",
    isinstance(body["available"], bool) and isinstance(body["detail"], str),
    {key: type(value).__name__ for key, value in body.items()},
)
check("status is public (no token, no 401)", "WWW-Authenticate" not in r.headers, r.status_code)
check(
    "AI_FORCE_FALLBACK=1 -> available False",
    body["available"] is False and "AI_FORCE_FALLBACK" in body["detail"],
    body["detail"],
)
check("status detail is an operator note, not a raw body", "data:" not in body["detail"].lower(),
      body["detail"][:120])

# --- 2. POST /api/ai/analyze: playground, unauthenticated ------------------- #
r = client.post("/api/ai/analyze", json={"language": "c", "code": BROKEN_C, "status": "compile_error"})
check("playground compile_error review -> 200 unauthenticated", r.status_code == 200, r.text[:200])
report = r.json()
check("analysis shape", set(report) == ANALYSIS_KEYS, sorted(report))
check("compile_error scores 0", report["suggested_score"] == 0.0, report["suggested_score"])
check("compile_error blames the compile stage", "編譯" in report["error_analysis"],
      report["error_analysis"][:60])

r = client.post("/api/ai/analyze", json={
    "language": "c", "code": BROKEN_C, "status": "compile_error",
    "compile_output": "solution.c:2:12: error: ‘undefined_symbol_here’ undeclared\n",
})
with_diag = r.json()
check("compiler diagnostics reach the analysis",
      with_diag["error_analysis"] != report["error_analysis"] and "第 2 行" in with_diag["error_analysis"],
      with_diag["error_analysis"][:90])

r = client.post("/api/ai/analyze", json={"language": "c", "code": NESTED_C, "status": "time_limit_exceeded"})
report = r.json()
check("TLE nested loops -> O(N^2)", report["time_complexity"] == "O(N^2)", report["time_complexity"])
check("TLE analysis blames the algorithm", "時間限制" in report["error_analysis"],
      report["error_analysis"][:60])
check("TLE score stays low but not zero", 0.0 < report["suggested_score"] < 55.0,
      report["suggested_score"])

r = client.post("/api/ai/analyze", json={"language": "c", "code": CORRECT_C, "status": "accepted"})
check("accepted playground review -> 200 unauthenticated", r.status_code == 200, r.text[:160])
report = r.json()
check("accepted suggested_score >= 80", report["suggested_score"] >= 80.0, report["suggested_score"])
check("accepted review is phrased as a pass",
      "全部測資" in report["error_analysis"] or "通過" in report["summary"], report["summary"][:60])
check("suggestions are a 2-4 bullet list",
      isinstance(report["suggestions"], list) and 2 <= len(report["suggestions"]) <= 4,
      report["suggestions"])
check("feedback_markdown is a rendered document",
      report["feedback_markdown"].startswith("###") and "改進建議" in report["feedback_markdown"],
      report["feedback_markdown"][:40])
check("fallback mode reports ai_powered=False + the heuristic model",
      report["ai_powered"] is False and report["model"] == ai_tutor.HEURISTIC_MODEL_NAME,
      (report["ai_powered"], report["model"]))
check("fallback detail names the reason", "heuristic" in report["detail"], report["detail"])
check("analyze is stateless: the same request gives the same answer",
      client.post("/api/ai/analyze", json={"language": "c", "code": CORRECT_C,
                                          "status": "accepted"}).json() == report)

r = client.post("/api/ai/analyze", json={"language": "cpp", "code": NESTED_CPP, "status": "wrong_answer"})
check("cpp + wrong_answer accepted", r.status_code == 200 and r.json()["error_analysis"], r.text[:160])
check("cpp nesting also profiles O(N^2)", r.json()["time_complexity"] == "O(N^2)",
      r.json()["time_complexity"])
r = client.post("/api/ai/analyze", json={"language": "python", "code": CORRECT_C})
check("unsupported language -> 422", r.status_code == 422, r.status_code)
r = client.post("/api/ai/analyze", json={"language": "c", "code": ""})
check("empty code -> 422", r.status_code == 422, r.status_code)
r = client.post("/api/ai/analyze", json={"language": "c", "code": "int main(){return 0;}", "problem_id": 0})
check("problem_id below 1 -> 422", r.status_code == 422, r.status_code)
r = client.post("/api/ai/analyze", json={"language": "c", "code": CORRECT_C})
check("missing status defaults to unknown", r.status_code == 200, r.text[:120])
check("an ungraded snippet still gets a review",
      bool(r.json()["summary"]) and 0.0 <= r.json()["suggested_score"] <= 100.0,
      r.json()["suggested_score"])
r = client.post("/api/ai/analyze", json={"language": "c", "code": CORRECT_C, "status": "nonsense-verdict"})
check("an unrecognised verdict degrades to unknown, not 500",
      r.status_code == 200 and bool(r.json()["error_analysis"]), r.status_code)

# --- 2b. POST /api/ai/analyze with problem context -------------------------- #
r = client.post("/api/ai/analyze", json={"language": "c", "code": CORRECT_C, "status": "accepted",
                                         "problem_id": TWO_SUM})
check("anonymous + problem_id -> 401", r.status_code == 401, r.text[:160])
r = call("post", "/api/ai/analyze", "not-a-jwt-at-all",
         json={"language": "c", "code": CORRECT_C, "problem_id": TWO_SUM})
check("garbage token + problem_id -> 401", r.status_code == 401, r.text[:160])
r = call("post", "/api/ai/analyze", BOB, json={"language": "c", "code": CORRECT_C,
                                              "status": "accepted", "problem_id": TWO_SUM})
check("unenrolled student + problem_id -> 403", r.status_code == 403, r.text[:160])
r = call("post", "/api/ai/analyze", ALICE, json={"language": "c", "code": CORRECT_C,
                                                "status": "accepted", "problem_id": TWO_SUM})
check("enrolled student + problem_id -> 200", r.status_code == 200, r.text[:200])
student_analysis = r.json()
r = call("post", "/api/ai/analyze", TEACHER, json={"language": "c", "code": CORRECT_C,
                                                  "status": "accepted", "problem_id": TWO_SUM})
check("problem teacher + problem_id -> 200", r.status_code == 200, r.text[:200])
teacher_analysis = r.json()
check("teacher persona switches the report heading",
      "教師閱卷參考" in teacher_analysis["feedback_markdown"]
      and "教師閱卷參考" not in student_analysis["feedback_markdown"],
      teacher_analysis["feedback_markdown"][:30])
r = call("post", "/api/ai/analyze", ALICE, json={"language": "c", "code": CORRECT_C, "problem_id": 999999})
check("unknown problem_id -> 404", r.status_code == 404, r.status_code)

# --- 2c. hidden test cases must never leak ---------------------------------- #
for who, analysis in (("student", student_analysis), ("teacher", teacher_analysis)):
    check(f"hidden case data absent from the {who} analysis",
          not leaks(analysis["summary"], analysis["error_analysis"], analysis["feedback_markdown"],
                    analysis["suggestions"], analysis["detail"]),
          leaks(analysis))
check("no analyze response ever carried hidden data", not leaks(student_analysis, teacher_analysis),
      leaks(student_analysis, teacher_analysis))

# --- 2d. live vLLM, only when the endpoint actually answers ----------------- #
os.environ["AI_FORCE_FALLBACK"] = "0"
LIVE_AI = False
try:
    probe = ai_tutor.check_ai_status()
    LIVE_AI = bool(probe["available"])
    r = client.get("/api/ai/status")
    check("GET /api/ai/status agrees with check_ai_status()",
          r.json()["available"] == LIVE_AI, (r.json()["available"], LIVE_AI, probe["detail"]))
    if LIVE_AI:
        check("live status detail explains the answer", bool(probe["detail"]), probe["detail"][:120])
        for who, token in (("student", ALICE), ("teacher", TEACHER)):
            r = call("post", "/api/ai/analyze", token, json={
                "language": "c", "code": CORRECT_C, "status": "accepted", "problem_id": TWO_SUM,
            })
            live = r.json()
            check(f"live vLLM analyze -> 200 ({who})", r.status_code == 200, r.text[:200])
            check(f"live analysis keeps the contract shape ({who})",
                  set(live) == ANALYSIS_KEYS, sorted(live))
            check(f"live analysis reports a consistent model ({who})",
                  live["model"] == (ai_tutor.VLLM_MODEL if live["ai_powered"]
                                    else ai_tutor.HEURISTIC_MODEL_NAME),
                  (live["ai_powered"], live["model"], live["detail"]))
            check(f"live analysis is well-formed ({who})",
                  bool(live["summary"]) and live["feedback_markdown"].startswith("###")
                  and 2 <= len(live["suggestions"]) <= 4 and 0.0 <= live["suggested_score"] <= 100.0
                  and bool(live["time_complexity"]),
                  (live["summary"][:40], len(live["suggestions"]), live["suggested_score"]))
            check(f"live vLLM never echoes hidden cases ({who})",
                  not leaks(live), leaks(live))
        r = client.post("/api/ai/analyze", json={"language": "c", "code": NESTED_C,
                                                "status": "time_limit_exceeded"})
        check("live playground analyze -> 200", r.status_code == 200, r.text[:160])
        check("live playground complexity token is a short string",
              bool(r.json()["time_complexity"]) and len(r.json()["time_complexity"]) <= 64,
              r.json()["time_complexity"])
finally:
    os.environ["AI_FORCE_FALLBACK"] = "1"

# --- 3. POST /api/submissions/{id}/ai-review -------------------------------- #
r = call("post", f"/api/problems/{TWO_SUM}/submit", ALICE, json={"language": "c", "code": CORRECT_C})
graded = r.json()
check("student solution accepted (fixture)", graded["status"] == "accepted", r.text[:200])
SUB_ID = graded["submission_id"]
check("submission row created (fixture)", isinstance(SUB_ID, int), SUB_ID)

r = call("get", f"/api/submissions/{SUB_ID}", ALICE)
fresh = r.json()
check("a new submission carries no review data yet",
      all(fresh[name] is None for name in REVIEW_FIELDS),
      {name: fresh[name] for name in REVIEW_FIELDS})

with SessionLocal() as session:
    alice_row = session.query(User).filter(User.username == "student1").one()
    stale_token, _ = auth_module.create_access_token(alice_row, ttl_seconds=-120)
r = call("post", f"/api/submissions/{SUB_ID}/ai-review", stale_token)
check("expired token cannot trigger an ai-review -> 401",
      r.status_code == 401 and "expired" in r.text.lower(), r.text[:160])
r = call("post", f"/api/submissions/{SUB_ID}/ai-review")
check("ai-review without a token -> 401", r.status_code == 401, r.text[:160])
r = call("post", f"/api/submissions/{SUB_ID}/ai-review", BOB)
check("unrelated student ai-review -> 403", r.status_code == 403, r.text[:160])
r = call("post", f"/api/submissions/{SUB_ID}/ai-review", TEACHER2)
check("peer teacher ai-review -> 403", r.status_code == 403, r.text[:160])
r = call("post", "/api/submissions/424242/ai-review", ADMIN)
check("ai-review of an unknown submission -> 404", r.status_code == 404, r.status_code)

r = call("post", f"/api/submissions/{SUB_ID}/ai-review", ALICE)
check("author ai-review -> 200", r.status_code == 200, r.text[:250])
review = r.json()
analysis = review["analysis"]
check("ai-review returns the submission detail plus an analysis",
      {"code", "analysis"} <= set(review) and set(analysis) == ANALYSIS_KEYS, sorted(review))
check("ai-review returns the author's own source", "scanf" in review["code"], review["code"][:40])
check("ai_feedback is exactly the rendered markdown",
      review["ai_feedback"] == analysis["feedback_markdown"], repr(review["ai_feedback"][:40]))
check("ai_suggested_score mirrors the analysis",
      review["ai_suggested_score"] == analysis["suggested_score"],
      (review["ai_suggested_score"], analysis["suggested_score"]))
check("ai_model mirrors the analysis", review["ai_model"] == analysis["model"], review["ai_model"])
check("ai_reviewed_at is stamped as UTC",
      review["ai_reviewed_at"] is not None and review["ai_reviewed_at"].endswith("+00:00"),
      review["ai_reviewed_at"])
row = submission_row(SUB_ID)
check("ai review persisted on the row",
      row.ai_feedback == analysis["feedback_markdown"]
      and row.ai_suggested_score == analysis["suggested_score"]
      and row.ai_model == analysis["model"] and row.ai_reviewed_at is not None,
      (row.ai_model, row.ai_suggested_score, row.ai_reviewed_at))
check("student persona gets the coach heading",
      "規則式" in review["ai_feedback"] and "教師閱卷參考" not in review["ai_feedback"],
      review["ai_feedback"][:30])
check("the AI suggestion is advisory: the judge score is untouched",
      review["score"] == 100.0 and row.score == 100.0, (review["score"], row.score))
check("an ai-review never writes teacher_score", review["teacher_score"] is None,
      review["teacher_score"])
STUDENT_REVIEWED_AT = row.ai_reviewed_at

r = call("post", f"/api/submissions/{SUB_ID}/ai-review", TEACHER)
check("problem teacher ai-review -> 200", r.status_code == 200, r.text[:200])
teacher_review = r.json()
check("teacher ai-review overwrites the stored feedback",
      "教師閱卷參考" in teacher_review["ai_feedback"], teacher_review["ai_feedback"][:30])
check("teacher ai-review re-stamps ai_reviewed_at",
      submission_row(SUB_ID).ai_reviewed_at >= STUDENT_REVIEWED_AT,
      (STUDENT_REVIEWED_AT, submission_row(SUB_ID).ai_reviewed_at))
check("ai-review stores nothing about hidden cases", not leaks(teacher_review), leaks(teacher_review))

if LIVE_AI:
    os.environ["AI_FORCE_FALLBACK"] = "0"
    try:
        r = call("post", f"/api/submissions/{SUB_ID}/ai-review", TEACHER)
        live_review = r.json()
        check("live ai-review -> 200", r.status_code == 200, r.text[:200])
        check("live ai-review persists what it returns",
              live_review["ai_feedback"] == live_review["analysis"]["feedback_markdown"]
              and live_review["ai_model"] == live_review["analysis"]["model"]
              and live_review["ai_suggested_score"] == live_review["analysis"]["suggested_score"]
              and live_review["ai_reviewed_at"] is not None,
              (live_review["ai_model"], live_review["ai_reviewed_at"]))
        check("live ai-review still redacts hidden cases", not leaks(live_review), leaks(live_review))
    finally:
        os.environ["AI_FORCE_FALLBACK"] = "1"

#: What section 4 and 4b must find stored, whatever reviewer answered last.
EXPECTED_AI_ROW = submission_row(SUB_ID)
EXPECTED_FEEDBACK = EXPECTED_AI_ROW.ai_feedback
EXPECTED_MODEL = EXPECTED_AI_ROW.ai_model
EXPECTED_SCORE = EXPECTED_AI_ROW.ai_suggested_score
check("some ai_feedback is stored before teacher grading starts",
      bool(EXPECTED_FEEDBACK) and bool(EXPECTED_MODEL), (EXPECTED_MODEL, EXPECTED_SCORE))

# --- 4. PATCH /api/submissions/{id}/review ---------------------------------- #
GRADED = {"teacher_score": 92.5, "teacher_feedback": "Good effort, watch overflow."}
r = call("patch", f"/api/submissions/{SUB_ID}/review", ALICE, json=GRADED)
check("student cannot grade -> 403", r.status_code == 403, r.text[:160])
r = call("patch", f"/api/submissions/{SUB_ID}/review", BOB, json=GRADED)
check("other student cannot grade -> 403", r.status_code == 403, r.text[:160])
r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER2, json=GRADED)
check("unrelated teacher cannot grade -> 403", r.status_code == 403, r.text[:160])
r = call("patch", f"/api/submissions/{SUB_ID}/review", json=GRADED)
check("anonymous grade -> 401", r.status_code == 401, r.text[:160])
for bad in (105, 105.0, -5, -0.01, 1000):
    r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json={"teacher_score": bad})
    check(f"out-of-bounds teacher_score {bad} -> 422", r.status_code == 422, (bad, r.status_code))
r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json={"teacher_feedback": "x" * 10_001})
check("over-long feedback -> 422", r.status_code == 422, r.status_code)
r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json={"teacher_score": "excellent"})
check("non-numeric score -> 422", r.status_code == 422, r.status_code)
row = submission_row(SUB_ID)
check("rejected PATCH attempts persisted nothing",
      row.teacher_score is None and row.teacher_feedback is None
      and row.reviewed_by_id is None and row.reviewed_at is None,
      (row.teacher_score, row.teacher_feedback, row.reviewed_by_id, row.reviewed_at))
check("rejected PATCH attempts left the ai review intact",
      row.ai_feedback == EXPECTED_FEEDBACK and row.ai_model == EXPECTED_MODEL, "compare")

r = call("patch", f"/api/submissions/{SUB_ID}/review", ADMIN,
         json={"teacher_score": 88.0, "teacher_feedback": "admin pass"})
check("admin may grade as well -> 200",
      r.status_code == 200 and r.json()["reviewed_by_id"] == ADMIN_ID, r.text[:200])

r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json=GRADED)
check("owning teacher grade -> 200", r.status_code == 200, r.text[:250])
graded_row = r.json()
check("teacher_score persisted", graded_row["teacher_score"] == 92.5, graded_row["teacher_score"])
check("teacher_feedback persisted",
      graded_row["teacher_feedback"] == "Good effort, watch overflow.", graded_row["teacher_feedback"])
check("reviewed_by_id is the grading teacher", graded_row["reviewed_by_id"] == TEACHER_ID,
      (graded_row["reviewed_by_id"], TEACHER_ID))
check("reviewed_at stamped as UTC",
      graded_row["reviewed_at"] is not None and graded_row["reviewed_at"].endswith("+00:00"),
      graded_row["reviewed_at"])
row = submission_row(SUB_ID)
check("teacher review persisted on the row",
      row.teacher_score == 92.5 and row.teacher_feedback == "Good effort, watch overflow."
      and row.reviewed_by_id == TEACHER_ID and row.reviewed_at is not None,
      (row.teacher_score, row.teacher_feedback, row.reviewed_by_id, row.reviewed_at))
check("teacher review does not clobber the ai review", row.ai_feedback == EXPECTED_FEEDBACK, "compare")
check("a teacher override never rewrites the judge score",
      graded_row["score"] == 100.0 and row.score == 100.0, (graded_row["score"], row.score))
check("boundary scores are accepted", call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER,
                                          json={"teacher_score": 100.0}).json()["teacher_score"] == 100.0
      and call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER,
               json={"teacher_score": 0.0}).json()["teacher_score"] == 0.0)

r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER,
         json={"teacher_feedback": "   ", "teacher_score": None})
check("blank feedback normalises to NULL",
      r.status_code == 200 and r.json()["teacher_feedback"] is None, r.text[:200])
check("an explicit null clears the manual score", r.json()["teacher_score"] is None,
      r.json()["teacher_score"])
r = call("patch", "/api/submissions/424242/review", TEACHER, json=GRADED)
check("grading an unknown submission -> 404", r.status_code == 404, r.status_code)
r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json={})
check("empty body is an accepted no-op", r.status_code == 200, r.text[:200])
r = call("patch", f"/api/submissions/{SUB_ID}/review", TEACHER, json=GRADED)
check("re-grading overwrites cleanly -> 200", r.status_code == 200, r.text[:200])

# --- 4b. the history views expose both review layers ------------------------ #
r = call("get", f"/api/submissions/{SUB_ID}", ALICE)
detail = r.json()
check("detail exposes the AI fields",
      detail["ai_feedback"] == EXPECTED_FEEDBACK
      and detail["ai_suggested_score"] == EXPECTED_SCORE
      and detail["ai_model"] == EXPECTED_MODEL and detail["ai_reviewed_at"] is not None,
      {name: detail[name] for name in ("ai_model", "ai_suggested_score", "ai_reviewed_at")})
check("detail exposes the teacher fields",
      detail["teacher_score"] == 92.5 and detail["teacher_feedback"] == GRADED["teacher_feedback"]
      and detail["reviewed_by_id"] == TEACHER_ID and detail["reviewed_at"] is not None,
      {name: detail[name] for name in ("teacher_score", "reviewed_by_id", "reviewed_at")})
check("detail keeps the judge score and the override apart",
      detail["score"] != detail["teacher_score"], (detail["score"], detail["teacher_score"]))
check("detail never carries hidden test data", not leaks(detail), leaks(detail))

r = call("get", "/api/submissions", ALICE)
rows = {row["id"]: row for row in r.json()["submissions"]}
check("student history includes the reviewed row", SUB_ID in rows, sorted(rows))
listed = rows.get(SUB_ID, {})
check("history row carries the AI fields",
      listed.get("ai_feedback") == EXPECTED_FEEDBACK and listed.get("ai_model") == EXPECTED_MODEL
      and listed.get("ai_suggested_score") == EXPECTED_SCORE
      and listed.get("ai_reviewed_at") is not None,
      {name: listed.get(name) for name in ("ai_model", "ai_reviewed_at")})
check("history row carries the teacher fields",
      listed.get("teacher_score") == 92.5
      and listed.get("teacher_feedback") == GRADED["teacher_feedback"]
      and listed.get("reviewed_by_id") == TEACHER_ID and listed.get("reviewed_at") is not None,
      {name: listed.get(name) for name in ("teacher_score", "teacher_feedback", "reviewed_by_id")})
check("history rows never carry the source code", "code" not in listed, sorted(listed)[:8])
r = call("get", "/api/submissions", TEACHER)
check("teacher history applies the same review fields",
      any(row["id"] == SUB_ID and row["teacher_score"] == 92.5 and row["ai_model"]
          for row in r.json()["submissions"]),
      [(row["id"], row["teacher_score"]) for row in r.json()["submissions"]])
r = call("get", "/api/submissions", BOB)
check("unrelated student still sees no rows", r.json()["total"] == 0, r.json()["total"])

# --- 5. in-place schema upgrade for pre-Phase-4 databases ------------------- #
legacy_columns = {name for name, _ in SUBMISSION_MIGRATIONS}
orm_columns = {column.name for column in Submission.__table__.columns}
check("SUBMISSION_MIGRATIONS lists the 8 Phase 4 + 8 Stage 4 columns",
      len(SUBMISSION_MIGRATIONS) == 16
      and tuple(name for name, _ in SUBMISSION_MIGRATIONS[:8]) == REVIEW_FIELDS
      and legacy_columns <= orm_columns,
      (len(SUBMISSION_MIGRATIONS), sorted(legacy_columns - orm_columns)))
check("every migrated column reaches a response schema",
      set(REVIEW_FIELDS) <= set(main.SubmissionOut.model_fields)
      and set(RUBRIC_FIELDS) <= set(main.RubricScores.model_fields)
      and set(FLAG_FIELDS) <= set(main.SubmissionDetailOut.model_fields),
      sorted((set(RUBRIC_FIELDS) | set(FLAG_FIELDS))
             - set(main.SubmissionDetailOut.model_fields)
             - set(main.RubricScores.model_fields)))

_MIG_FD, _MIG_PATH = tempfile.mkstemp(prefix="codingpass-legacy-", suffix=".db")
os.close(_MIG_FD)
os.unlink(_MIG_PATH)
legacy_engine = create_engine(f"sqlite:///{_MIG_PATH}")
with legacy_engine.begin() as connection:
    connection.execute(text(
        """
        CREATE TABLE submissions (
            id INTEGER NOT NULL PRIMARY KEY,
            problem_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            language VARCHAR(8) NOT NULL,
            code TEXT NOT NULL,
            status VARCHAR(24) NOT NULL,
            score FLOAT NOT NULL,
            passed_count INTEGER NOT NULL,
            total_count INTEGER NOT NULL,
            execution_time_ms FLOAT NOT NULL,
            created_at DATETIME NOT NULL
        )
        """
    ))
    before = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(submissions)"))}
check("legacy fixture really lacks the Phase 4 columns",
      before.isdisjoint(legacy_columns) and len(before) == 11, sorted(before))

database._ensure_submission_columns(legacy_engine)
with legacy_engine.begin() as connection:
    after = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(submissions)"))}
    connection.execute(text(
        "INSERT INTO submissions (id, problem_id, student_id, language, code, status, score,"
        " passed_count, total_count, execution_time_ms, created_at, ai_feedback,"
        " ai_suggested_score, ai_model, teacher_feedback, teacher_score, reviewed_by_id)"
        " VALUES (1, 1, 1, 'c', 'int main(){}', 'accepted', 100.0, 7, 7, 12.5,"
        " '2026-01-01 00:00:00', 'ok', 90.0, 'heuristic-rules', 'nice', 92.5, 2)"
    ))
    stored = connection.execute(text(
        "SELECT ai_feedback, ai_suggested_score, ai_model, ai_reviewed_at, teacher_feedback,"
        " teacher_score, reviewed_by_id, reviewed_at FROM submissions WHERE id = 1"
    )).fetchone()
check("migration adds all 16 columns", legacy_columns <= after, sorted(legacy_columns - after))
check("migration keeps the original columns", before <= after, sorted(before - after))
check("migrated table is readable and writable",
      stored[0] == "ok" and stored[1] == 90.0 and stored[2] == "heuristic-rules"
      and stored[3] is None and stored[4] == "nice" and stored[5] == 92.5
      and stored[6] == 2 and stored[7] is None, tuple(stored))
column_count = len(after)
database._ensure_submission_columns(legacy_engine)  # a second boot must be a no-op
with legacy_engine.begin() as connection:
    again = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(submissions)"))}
check("migration is idempotent", again == after and len(again) == column_count, len(again))
legacy_engine.dispose()

with database.engine.begin() as connection:
    live_columns = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(submissions)"))}
check("a freshly created table already ships every column", legacy_columns <= live_columns,
      sorted(legacy_columns - live_columns))
database._ensure_submission_columns(database.engine)
check("running the migration on a fresh database changes nothing",
      len(live_columns) == len(orm_columns), (len(live_columns), len(orm_columns)))

# --- known gap (pinned so the fix is a deliberate test change) -------------- #
r = client.post("/api/ai/analyze", json={
    "language": "c", "status": "time_limit_exceeded",
    "code": "#include <stdio.h>\nint main(void){ int n,i,j; long long s=0;"
            ' if(scanf("%d",&n)!=1)return 1;\n'
            " for(i=0;i<n;i++){ for(j=0;j<n;j++){ s+=i*j; } }"
            ' printf("%lld\\n",s); return 0; }\n',
})
check(
    "KNOWN GAP: a for/for pair is profiled O(N) because _NESTED_LOOP_RE cannot"
    " cross a for-header semicolon (asserted, so fixing it updates this test"
    " on purpose)",
    r.status_code == 200 and r.json()["time_complexity"] == "O(N)",
    r.json().get("time_complexity"),
)

leftovers = list(pathlib.Path("/tmp").glob("cj-*"))
check("sandbox temp dirs cleaned", not leftovers, [str(p) for p in leftovers][:3])
check("only Alice's own history is visible to her",
      all(row["student_id"] == ALICE_ID for row in call("get", "/api/submissions", ALICE).json()["submissions"]))
with SessionLocal() as session:
    check("the reviewed row still belongs to Alice",
          submission_row(SUB_ID).student_id == ALICE_ID
          and session.query(User).filter(User.id == ALICE_ID).one().username == "student1")

client.__exit__(None, None, None)
print()
print("FAILED:", failures if failures else "none")
print(f"({len(failures)} failed of the checks printed above; db kept at {_DB_PATH})")
print(f"live vLLM exercised: {LIVE_AI} ({ai_tutor.VLLM_BASE_URL} / {ai_tutor.VLLM_MODEL})")
sys.exit(1 if failures else 0)
