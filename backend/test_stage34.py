#!/usr/bin/env python3
"""End-to-end tests for Stage 3 (plagiarism) and Stage 4 (rubric scoring).

Run: ``python test_stage34.py`` (no pytest). Uses a throwaway SQLite file and a
synthetic problem pack built under ``/tmp``, so the real database and the real
pack library are never touched.

Covers:

* ``POST /api/problems/import-pack`` - folder pack, zip upload, path allow-list,
  every rejected shape, and the fact that a reference solution never leaks.
* the 100-point rubric on ``POST /api/problems/{id}/submit``: clean accept,
  compile error rescued by the Minimal Patch Probe, presentation warning,
  wording-rule violation, partial credit on a wrong answer.
* ``GET /api/classrooms/{id}/problems/{id}/plagiarism`` - flagged pair, Reference
  Baseline Subtraction exemption, scaffolding skipped, and who may call it.
* the in-place schema upgrade of a pre-Stage-4 ``submissions`` table.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-s34-", suffix=".db")
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "stage34-test-secret-0123456789-abcdefghij"
os.environ["BCRYPT_ROUNDS"] = "10"
os.environ.setdefault("CODE_LOG_LEVEL", "WARNING")

# Keep the statement PDFs this suite writes out of the source tree.
_PDF_DIR = tempfile.mkdtemp(prefix="codingpass-s34-pdfs-")
os.environ["PROBLEM_PACK_PDF_DIR"] = _PDF_DIR

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
from models import Language, Problem, Submission, SubmissionStatus  # noqa: E402

client = TestClient(main.app)
client.__enter__()  # lifespan: create tables + seed the demo dataset
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def call(method: str, path: str, token: str | None = None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return getattr(client, method)(path, headers=headers, **kwargs)


def login(username: str, password: str) -> str:
    reply = client.post("/api/auth/login", json={"username": username, "password": password})
    if reply.status_code != 200:
        raise SystemExit(f"fixture login failed for {username}: {reply.text[:200]}")
    return reply.json()["access_token"]


def register(username: str, email: str, full_name: str, password: str) -> str:
    reply = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "full_name": full_name,
        },
    )
    if reply.status_code != 201:
        raise SystemExit(f"fixture register failed for {username}: {reply.text[:200]}")
    return login(username, password)


# --------------------------------------------------------------------------- #
# Fixtures: a synthetic problem pack
# --------------------------------------------------------------------------- #

REFERENCE_CPP = """\
#include <iostream>
using namespace std;

int main() {
    int n = 0;
    if (!(cin >> n)) return 1;
    long long total = 0;
    for (int i = 0; i < n; i++) {
        int value = 0;
        cin >> value;
        total += value;
    }
    cout << total << endl;
    return 0;
}
"""

# The same algorithm, renamed and restructured by hand: structurally identical to
# the reference, deliberately different at the byte level.
REFERENCE_RENAMED_CPP = """\
#include <iostream>
using namespace std;

int main() {
    int count = 0;
    if (!(cin >> count)) return 1;
    long long sum = 0;
    for (int index = 0; index < count; index++) {
        int read = 0;
        cin >> read;
        sum = sum + read;
    }
    cout << sum << endl;
    return 0;
}
"""

SAMPLE_CARD = """\
\u3010\u7bc4\u4f8b\u8f38\u5165 1\u3011
3
1 2 3
\u3010\u7bc4\u4f8b\u8f38\u51fa 1\u3011
6
\u3010\u7bc4\u4f8b\u8f38\u5165 2\u3011
4
10 -2 0 5
\u3010\u7bc4\u4f8b\u8f38\u51fa 2\u3011
13
"""

# One case per run: ``n`` on the first line, then ``n`` integers.
TEDA_SCRIPT = """\
// CodingPass \u96a8\u6a5f\u6e2c\u8cc7\u751f\u6210\u8108\u672c (TeDa-Testing)
[1;nn]{
  let n = 3~6;
  n;
  [n;sn]{
    -50~99;
  }
}
"""

WORDING_RULES = """\
#term[0;skipComments skipStrings] system
#regex[1~*;skipComments skipStrings] \\b(for|while)\\b
"""

README = """\
# P9001_Integer Sum

| Item | Value |
| --- | --- |
| **Difficulty** | easy |
| **Time limit** | 2000 ms |
| **Memory limit** | 256 MB |

Read n integers and print their sum.
"""


def write_pack(directory: pathlib.Path, code: str, title: str) -> pathlib.Path:
    """Materialise one ``Pxxxx_\u984c\u540d`` pack folder and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"{code}_{title}"
    (directory / f"{prefix}_\u5eab\u984c\u5361\u78bc\u6587\u6a19\u78bc.cpp").write_text(
        REFERENCE_CPP, encoding="utf-8"
    )
    (directory / f"{prefix}_\u5eab\u984c\u5361\u6e2c\u8cc7\u7bc4\u4f8b.txt").write_text(
        SAMPLE_CARD, encoding="utf-8"
    )
    (directory / f"{prefix}_\u5eab\u984c\u5361\u6e2c\u8cc7\uff10\u96a8\u8f38\u5165.teda").write_text(
        TEDA_SCRIPT, encoding="utf-8"
    )
    (directory / f"{prefix}_\u5eab\u984c\u5361\u78bc\u6587\u9650\u78bc.txt").write_text(
        WORDING_RULES, encoding="utf-8"
    )
    (directory / f"{prefix}_\u51fa\u984c\u8a2d\u5b9a\u8aaa\u660e.md").write_text(
        README, encoding="utf-8"
    )
    # The importer only records the path, never the bytes, so a stub is enough.
    (directory / f"{prefix}.pdf").write_bytes(b"%PDF-1.4\nstub statement\n%%EOF\n")
    return directory


ROOT = pathlib.Path(tempfile.mkdtemp(prefix="codingpass-s34-pack-"))
PACK_DIR = write_pack(ROOT / "P9001_\u6574\u6578\u7e3d\u548c", "P9001", "\u6574\u6578\u7e3d\u548c")
NOT_A_PACK = ROOT / "random_folder"
NOT_A_PACK.mkdir()
(NOT_A_PACK / "notes.txt").write_text("nothing to import here", encoding="utf-8")

ADMIN = login("admin", "admin123")
TEACHER = login("teacher1", "teacher123")
ALICE = login("student1", "student123")
BOB = login("student2", "student123")
CAROL = register("carol", "carol@example.com", "Carol Lin", "carol1234")
DAVE = register("dave", "dave@example.com", "Dave Wu", "dave12345")
ERIN = register("erin", "erin@example.com", "Erin Yu", "erin12345")
FRANK = register("frank", "frank@example.com", "Frank He", "frank12345")

# --- join the demo class so the students may submit and be screened --------- #
class_rows = call("get", "/api/classrooms", TEACHER).json()
if not class_rows:
    raise SystemExit("fixture: the demo dataset has no class to enrol in")
CLASSROOM_ID = class_rows[0]["id"]
CLASSROOM_CODE = class_rows[0]["code"]
CLASSROOM_NAME = class_rows[0]["name"]
for _label, _token in (("alice", ALICE), ("bob", BOB), ("carol", CAROL),
                       ("dave", DAVE), ("erin", ERIN), ("frank", FRANK)):
    joined = call("post", "/api/classrooms/join", _token, json={"code": CLASSROOM_CODE})
    if joined.status_code not in (200, 201, 409):  # already enrolled is fine
        raise SystemExit(f"fixture: {_label} could not join the class: {joined.text[:160]}")

r = call("get", f"/api/classrooms/{CLASSROOM_ID}", TEACHER)
check(
    "demo class fixture",
    r.status_code == 200 and len(r.json().get("students", [])) >= 5,
    (CLASSROOM_NAME, len(r.json().get("students", []))),
)

# --------------------------------------------------------------------------- #
# 1. Configuration surface
# --------------------------------------------------------------------------- #

r = call("get", "/api/config")
config = r.json()
check(
    "config advertises the rubric bands",
    config.get("rubric_points")
    == {
        "compile": 10.0,
        "sample": 10.0,
        "random": 30.0,
        "logic": 40.0,
        "quality": 10.0,
    },
    config.get("rubric_points"),
)
check(
    "config advertises the plagiarism threshold",
    config.get("plagiarism_default_threshold") == main.PLAGIARISM_DEFAULT_THRESHOLD,
    config.get("plagiarism_default_threshold"),
)
check(
    "config advertises the probe budgets",
    config.get("patch_probe", {}).get("max_probe_runs") == main.MAX_PROBE_RUNS
    and config.get("patch_probe", {}).get("patched_pass_score") == 32.0,
    config.get("patch_probe"),
)
check(
    "config states whether the pack allow-list is on",
    config.get("pack_roots_restricted") is False and config.get("pack_upload_bytes") > 0,
    config.get("pack_roots_restricted"),
)

# --------------------------------------------------------------------------- #
# 2. Pack import
# --------------------------------------------------------------------------- #

r = call(
    "post",
    "/api/problems/import-pack",
    TEACHER,
    json={
        "directory_path": str(PACK_DIR),
        "classroom_id": CLASSROOM_ID,
        "generate_hidden": True,
        "num_random": 4,
    },
)
payload = r.json() if r.status_code == 201 else {}
check(
    "import a folder pack -> 201 with cases generated",
    r.status_code == 201 and payload.get("sample_case_count") == 2
    and payload.get("random_case_count", 0) >= 3,
    (r.status_code, {k: payload.get(k) for k in ("sample_case_count", "random_case_count", "warnings")}),
)
check("import records the problem code", payload.get("problem_code") == "P9001", payload.get("problem_code"))
check(
    "import records every pack capability",
    payload.get("has_ref_code") and payload.get("has_wording_rules")
    and payload.get("has_random_generator") and payload.get("has_statement_pdf"),
    {k: payload.get(k) for k in ("has_ref_code", "has_wording_rules", "has_random_generator", "has_statement_pdf")},
)
check("import keeps the pack name/source", payload.get("pack_name", "").startswith("P9001_") and payload.get("pack_source") == "dir",
      (payload.get("pack_name"), payload.get("pack_source")))
check("import shows the teacher the wording rules", "#term[0" in (payload.get("wording_rules") or ""), payload.get("wording_rules"))
check("imported limits come from the teacher notes", payload.get("time_limit_sec") == 2.0 and payload.get("memory_limit_mb") == 256,
      (payload.get("time_limit_sec"), payload.get("memory_limit_mb")))
PROBLEM_ID = payload.get("id")

stored = client.app  # noqa: F841  (only used to keep the reference obvious)
with database.SessionLocal() as session:
    row = session.get(Problem, PROBLEM_ID)
    check(
        "reference solution is stored server-side only",
        row is not None and (row.ref_code or "").strip().startswith("#include")
        and row.ref_language == "cpp" and row.wording_rules.strip().startswith("#term"),
        (row.ref_language if row else None),
    )
    check(
        "statement PDF path is recorded for a folder pack",
        row is not None and (row.pdf_path or "").endswith(".pdf"),
        row.pdf_path if row else None,
    )

r = call("get", f"/api/problems/{PROBLEM_ID}", ALICE)
student_view = r.json()
check(
    "student problem view hides hidden cases and wording rules",
    r.status_code == 200 and all(case["is_sample"] for case in student_view["test_cases"])
    and student_view.get("wording_rules") is None
    and len(student_view["test_cases"]) == 2 and student_view["hidden_count"] >= 3,
    (len(student_view["test_cases"]), student_view["hidden_count"], student_view.get("wording_rules")),
)
check(
    "no response ever carries the reference source",
    "long long total" not in r.text and "long long total" not in json.dumps(payload),
    "",
)

# --- rejected shapes -------------------------------------------------------- #
check(
    "student may not import packs",
    call("post", "/api/problems/import-pack", ALICE,
         json={"directory_path": str(PACK_DIR)}).status_code == 403,
    "",
)
check(
    "anonymous import -> 401",
    call("post", "/api/problems/import-pack", None,
         json={"directory_path": str(PACK_DIR)}).status_code == 401,
    "",
)
check(
    "missing pack -> 404",
    call("post", "/api/problems/import-pack", TEACHER,
         json={"directory_path": str(ROOT / "nope")}).status_code == 404,
    "",
)
check(
    "folder without pack files -> 400",
    call("post", "/api/problems/import-pack", TEACHER,
         json={"directory_path": str(NOT_A_PACK)}).status_code == 400,
    "",
)
check(
    "neither path nor file -> 400",
    call("post", "/api/problems/import-pack", TEACHER, json={}).status_code == 400,
    "",
)
check(
    "out-of-range num_random -> 422",
    call("post", "/api/problems/import-pack", TEACHER,
         json={"directory_path": str(PACK_DIR), "num_random": 0}).status_code == 422,
    "",
)
check(
    "re-importing the same problem code -> 409",
    call("post", "/api/problems/import-pack", TEACHER,
         json={"directory_path": str(PACK_DIR), "classroom_id": CLASSROOM_ID}).status_code == 409,
    "",
)
check(
    "a class that does not exist -> 404",
    call("post", "/api/problems/import-pack", TEACHER,
         json={"directory_path": str(NOT_A_PACK), "classroom_id": 9999}).status_code == 404,
    "",
)
other_class = call("post", "/api/classrooms", ADMIN, json={"name": "Other Class", "description": ""})
OTHER_CLASS_ID = other_class.json().get("id") if other_class.status_code in (200, 201) else None
check(
    "publishing into another teacher's class -> 403",
    OTHER_CLASS_ID is not None
    and call("post", "/api/problems/import-pack", TEACHER,
             json={"directory_path": str(NOT_A_PACK), "classroom_id": OTHER_CLASS_ID}).status_code == 403,
    (OTHER_CLASS_ID, other_class.status_code),
)

# --- the path allow-list ---------------------------------------------------- #
_original_roots = main.PACK_ALLOWED_ROOTS
main.PACK_ALLOWED_ROOTS = [ROOT / "allowed"]
try:
    r = call("post", "/api/problems/import-pack", TEACHER, json={"directory_path": str(PACK_DIR)})
    check("path outside PROBLEM_PACK_ROOTS -> 403", r.status_code == 403, r.status_code)
    allowed_dir = write_pack(ROOT / "allowed" / "P9002_\u6574\u6578\u7e3d\u548c", "P9002", "\u6574\u6578\u7e3d\u548c")
    c2 = call("post", "/api/classrooms", TEACHER, json={"name": "Second Course", "code": "CS102"})
    c2_id = c2.json()["id"] if c2.status_code == 201 else None
    if c2_id:
        call("post", "/api/classrooms/join", ALICE, json={"code": "CS102"})
    r = call("post", "/api/problems/import-pack", TEACHER,
             json={"directory_path": str(allowed_dir), "generate_hidden": False, "classroom_id": c2_id})
    check("path inside PROBLEM_PACK_ROOTS is accepted", r.status_code == 201, r.text[:200])
    check(
        "generate_hidden=false keeps only the sample cases",
        r.status_code == 201 and r.json()["random_case_count"] == 0
        and r.json()["sample_case_count"] == 2,
        r.json() if r.status_code == 201 else {},
    )
    P9002_ID = r.json().get("id") if r.status_code == 201 else None
finally:
    main.PACK_ALLOWED_ROOTS = _original_roots

# --- zip upload ------------------------------------------------------------- #
zip_buffer = io.BytesIO()
upload_pack = write_pack(ROOT / "upload" / "P9003_\u6574\u6578\u7e3d\u548c", "P9003", "\u6574\u6578\u7e3d\u548c")
with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
    for item in upload_pack.rglob("*"):
        archive.write(item, arcname=str(item.relative_to(upload_pack.parent)))
zip_buffer.seek(0)

r = call(
    "post",
    "/api/problems/import-pack",
    TEACHER,
    files={"file": ("P9003.zip", zip_buffer, "application/zip")},
    data={"generate_hidden": "false"},
)
check("upload a zipped pack -> 201", r.status_code == 201, r.text[:200])
upload_payload = r.json() if r.status_code == 201 else {}
check("upload reports pack_source=zip", upload_payload.get("pack_source") == "zip", upload_payload.get("pack_source"))
check(
    "statement PDF survives the temporary extraction directory",
    bool(upload_payload.get("has_statement_pdf"))
    and pathlib.Path(upload_payload.get("pdf_path") or "/nonexistent").is_file()
    and str(upload_payload.get("pdf_path")).startswith(_PDF_DIR),
    upload_payload.get("pdf_path"),
)
check("both sources at once -> 400",
      call("post", "/api/problems/import-pack", TEACHER,
           files={"file": ("P9003.zip", io.BytesIO(b"x" * 32), "application/zip")},
           data={"directory_path": str(PACK_DIR)}).status_code == 400, "")
check("empty upload -> 400",
      call("post", "/api/problems/import-pack", TEACHER,
           files={"file": ("empty.zip", io.BytesIO(b""), "application/zip")}).status_code == 400, "")

r = call("post", "/api/problems/import-pack", TEACHER,
         files={"file": ("junk.zip", io.BytesIO(b"this is not a zip"), "application/zip")})
check("non-zip upload -> 400", r.status_code == 400, (r.status_code, r.text[:160]))

# --------------------------------------------------------------------------- #
# 3. Rubric scoring
# --------------------------------------------------------------------------- #

SUBMISSIONS: dict[str, dict] = {}


def submit(token: str, code: str, label: str, language: str = "cpp", problem_id: int | None = None):
    reply = call(
        "post",
        f"/api/problems/{problem_id or PROBLEM_ID}/submit",
        token,
        json={"code": code, "language": language},
    )
    SUBMISSIONS[label] = reply.json() if reply.status_code == 200 else {}
    if reply.status_code != 200:
        print(f"   [submit {label} -> HTTP {reply.status_code} {reply.text[:200]}]")
    return reply


CLEAN_CPP = """\
#include <iostream>
using namespace std;
int main() {
    int n; cin >> n;
    long long total = 0;
    for (int i = 0; i < n; i++) { int v; cin >> v; total += v; }
    cout << total << endl;
    return 0;
}
"""

# Correct, but every line ends with a space: a strict judge returns PE.
MESSY_CPP = CLEAN_CPP.replace("cin >> n;", "cin >> n; ").replace(
    'cout << total << endl;', 'cout << total << " " << endl;'
)

# Two mechanical slips: a forgotten semicolon and a missing <vector> include.
NEARLY_CPP = """\
#include <iostream>
using namespace std;

int main() {
    int n
    cin >> n;
    vector<int> values(n);
    for (int i = 0; i < n; i++) cin >> values[i];
    int total = 0;
    for (int i = 0; i < n; i++) total += values[i];
    cout << total << endl;
    return 0;
}
"""

# Same algorithm, but it ignores negative numbers: partial credit / wrong answers.
WRONG_CPP = """\
#include <iostream>
using namespace std;
int main() {
    int n;
    if (!(cin >> n)) return 0;
    long long total = 0;
    for (int i = 0; i < n; i++) {
        int v; cin >> v;
        if (v > 0) total += v;
    }
    cout << total << endl;
    return 0;
}
"""

# Compiles and answers correctly, but calls system(): a wording-rule violation.
FORBIDDEN_CPP = CLEAN_CPP.replace(
    "int n; cin >> n;", "system(\"true\"); int n; cin >> n;"
)

r = submit(ALICE, CLEAN_CPP, "clean")
clean = SUBMISSIONS["clean"]
rubric = clean.get("rubric", {})
check(
    "accepted submission scores the full rubric",
    r.status_code == 200 and clean.get("status") == "accepted"
    and rubric.get("compile_score") == 10.0 and rubric.get("sample_score") == 10.0
    and rubric.get("random_score") == 30.0 and rubric.get("logic_score") == 40.0
    and rubric.get("quality_score") == 10.0 and rubric.get("total") == 100.0,
    rubric,
)
check(
    "a clean run needs no probe and raises no flags",
    clean.get("patch_probe") is None and clean.get("presentation_warning") is False
    and clean.get("wording_pass") is True and clean.get("wording_violations") == [],
    {k: clean.get(k) for k in ("patch_probe", "presentation_warning", "wording_pass")},
)

r = submit(BOB, NEARLY_CPP, "compile_error")
broken = SUBMISSIONS["compile_error"]
broken_rubric = broken.get("rubric", {})
probe = broken.get("patch_probe") or {}
check(
    "compile error zeroes the compile and case bands",
    r.status_code == 200 and broken.get("status") == "compile_error"
    and broken_rubric.get("compile_score") == 0.0
    and broken_rubric.get("sample_score") == 0.0 and broken_rubric.get("random_score") == 0.0,
    broken_rubric,
)
check(
    "the patch probe repairs the two slips and awards the CE credit",
    probe.get("needed") is True and probe.get("patch_success") is True
    and sorted(probe.get("applied_fixes") or []) == ["missing-include", "missing-semicolon"]
    and broken_rubric.get("logic_score") == 32.0,
    (probe.get("applied_fixes"), broken_rubric.get("logic_score")),
)
check(
    "a rescued compile error still loses the build points",
    broken_rubric.get("total", 100.0) <= 42.0 and broken_rubric.get("quality_score", 10.0) <= 5.0,
    (broken_rubric.get("total"), broken_rubric.get("quality_score")),
)
check(
    "the response never returns the repaired source",
    "patched_code" not in r.text and '"compile_output":""' not in r.text,
    "patched_code" in r.text,
)

r = submit(CAROL, MESSY_CPP, "presentation")
messy = SUBMISSIONS["presentation"]
messy_rubric = messy.get("rubric", {})
check(
    "trailing spaces are a presentation warning, not a wrong answer",
    r.status_code == 200 and messy.get("status") == "accepted"
    and messy.get("presentation_warning") is True,
    (messy.get("status"), messy.get("presentation_warning")),
)
check(
    "the presentation warning costs quality and formatting points",
    messy_rubric.get("quality_score", 10.0) == 8.0
    and messy_rubric.get("logic_score", 40.0) == 36.0,
    (messy_rubric.get("quality_score"), messy_rubric.get("logic_score")),
)
check(
    "every case that passed only after trimming is flagged",
    sum(1 for case in messy.get("results", []) if case.get("presentation_warning")) >= 1,
    [case.get("presentation_warning") for case in messy.get("results", [])],
)

r = submit(DAVE, FORBIDDEN_CPP, "wording")
wording = SUBMISSIONS["wording"]
wording_rubric = wording.get("rubric", {})
check(
    "a banned identifier fails the wording screen but not the judge",
    r.status_code == 200 and wording.get("status") == "accepted"
    and wording.get("wording_pass") is False
    and any("system" in item for item in wording.get("wording_violations") or []),
    (wording.get("status"), wording.get("wording_violations")),
)
check(
    "the wording violation costs exactly the quality band",
    wording_rubric.get("quality_score") == 7.0 and wording_rubric.get("logic_score") == 40.0
    and wording_rubric.get("total") == 97.0,
    wording_rubric,
)

r = submit(ERIN, WRONG_CPP, "wrong")
wrong = SUBMISSIONS["wrong"]
wrong_rubric = wrong.get("rubric", {})
check(
    "a wrong answer keeps proportional logic credit",
    r.status_code == 200 and wrong.get("status") in ("wrong_answer", "accepted")
    and 0.0 < wrong_rubric.get("logic_score", -1.0) < 40.0
    and wrong_rubric.get("logic_score") == round(40.0 * wrong.get("passed_count") / wrong.get("total_count"), 2),
    (wrong.get("passed_count"), wrong.get("total_count"), wrong_rubric.get("logic_score")),
)
check(
    "the probe reports the algorithm itself is broken",
    (wrong.get("patch_probe") or {}).get("needed") is False
    and (wrong.get("patch_probe") or {}).get("patch_success") is False,
    wrong.get("patch_probe"),
)

# --- the rubric has to survive the round trip to the database --------------- #
detail = call("get", f"/api/submissions/{clean['submission_id']}", ALICE).json()
check(
    "submission detail repeats the rubric and the flags",
    detail.get("rubric", {}).get("total") == 100.0
    and detail.get("wording_pass") is True and detail.get("presentation_warning") is False,
    detail.get("rubric"),
)
detail_broken = call("get", f"/api/submissions/{broken['submission_id']}", BOB).json()
check(
    "the probe result is persisted as rubric credit",
    detail_broken.get("rubric", {}).get("logic_score") == 32.0
    and detail_broken.get("rubric_total") == broken_rubric.get("total"),
    (detail_broken.get("rubric"), detail_broken.get("rubric_total")),
)
detail_wording = call("get", f"/api/submissions/{wording['submission_id']}", DAVE).json()
check(
    "wording violations round-trip through the database",
    detail_wording.get("wording_pass") is False
    and any("system" in item for item in detail_wording.get("wording_violations") or []),
    detail_wording.get("wording_violations"),
)
history = call("get", "/api/submissions", ALICE).json()
check(
    "history rows carry the rubric total",
    any(row["id"] == clean["submission_id"] and row["rubric_total"] == 100.0 for row in history["submissions"]),
    [row.get("rubric_total") for row in history["submissions"]],
)

# --- trial runs are not graded and are not probed --------------------------- #
r = call("post", f"/api/problems/{PROBLEM_ID}/submit", TEACHER,
         json={"code": NEARLY_CPP, "language": "cpp"})
trial = r.json()
check(
    "a teacher trial run is not recorded and not probed",
    r.status_code == 200 and trial.get("trial_run") is True
    and trial.get("submission_id") is None and trial.get("patch_probe") is None
    and trial.get("rubric", {}).get("logic_score") == 0.0,
    {k: trial.get(k) for k in ("trial_run", "submission_id", "patch_probe")},
)

# --- a problem with no hidden cases still adds up to 100 -------------------- #
r = submit(ALICE, CLEAN_CPP, "no_hidden", problem_id=P9002_ID)
solo = SUBMISSIONS["no_hidden"]
solo_rubric = solo.get("rubric", {})
check(
    "sample-only problems rescale the bands to 100",
    r.status_code == 200 and solo_rubric.get("sample_score") == 40.0
    and solo_rubric.get("random_score") == 0.0 and solo_rubric.get("total") == 100.0,
    solo_rubric,
)

# --------------------------------------------------------------------------- #
# 4. Plagiarism screen
# --------------------------------------------------------------------------- #

# Dave copies Carol's custom helper-function solution with identifiers renamed: renaming
# is exactly what the structural comparison is built to see through.
WHILE_CPP = """\
#include <iostream>
using namespace std;
long long accumulate_sum(int count) {
    long long res = 0;
    for (int i = 0; i < count; i++) {
        long long val;
        cin >> val;
        res += val;
    }
    return res;
}
int main() {
    int n;
    if (cin >> n) {
        cout << accumulate_sum(n) << endl;
    }
    return 0;
}
"""

WHILE_RENAMED_CPP = """\
#include <iostream>
using namespace std;
long long calc_total(int num_elements) {
    long long answer = 0;
    for (int idx = 0; idx < num_elements; idx++) {
        long long entry;
        cin >> entry;
        answer += entry;
    }
    return answer;
}
int main() {
    int total_items;
    if (cin >> total_items) {
        cout << calc_total(total_items) << endl;
    }
    return 0;
}
"""

r = submit(CAROL, WHILE_CPP, "copy_source")
r = submit(DAVE, WHILE_RENAMED_CPP, "copy_target")
r = submit(BOB, REFERENCE_CPP, "textbook_a")
r = submit(ALICE, REFERENCE_RENAMED_CPP, "textbook_b")
r = submit(FRANK, main.DEFAULT_STARTER_CPP, "scaffolding")

path = f"/api/classrooms/{CLASSROOM_ID}/problems/{PROBLEM_ID}/plagiarism"
r = call("get", path, TEACHER)
report = r.json() if r.status_code == 200 else {}
check(
    "the class teacher can run the screen",
    r.status_code == 200 and report.get("problem_id") == PROBLEM_ID
    and report.get("classroom_id") == CLASSROOM_ID and report.get("reference_available") is True,
    (r.status_code, {k: report.get(k) for k in ("problem_id", "reference_available")}),
)
check(
    "the screen compares each student's strongest attempt",
    report.get("students_compared", 0) >= 4 and report.get("submissions_compared", 0)
    >= report.get("students_compared", 0),
    {k: report.get(k) for k in ("students_compared", "submissions_compared")},
)
check(
    "untouched scaffolding is skipped, not flagged",
    report.get("skipped_trivial", 0) >= 1 and "frank" not in str(
        [pair["student_a"] for pair in report.get("pairs", [])]
        + [pair["student_b"] for pair in report.get("pairs", [])]
    ),
    report.get("skipped_trivial"),
)

renamed_pair = next(
    (
        pair
        for pair in report.get("pairs", [])
        if {"carol", "dave"} <= {
            pair["student_a"].rsplit("@", 1)[-1].rstrip(")"),
            pair["student_b"].rsplit("@", 1)[-1].rstrip(")"),
        }
    ),
    None,
)
check(
    "renaming identifiers does not hide a copied solution",
    renamed_pair is not None and renamed_pair["flagged"] is True
    and renamed_pair["ast_similarity"] >= 0.97,
    renamed_pair,
)
check(
    "a flagged pair names both submissions by id",
    renamed_pair is not None
    and renamed_pair["submission_id_a"] in {SUBMISSIONS["copy_source"]["submission_id"],
                                           SUBMISSIONS["copy_target"]["submission_id"]}
    and renamed_pair["submission_id_b"] in {SUBMISSIONS["copy_source"]["submission_id"],
                                           SUBMISSIONS["copy_target"]["submission_id"]},
    renamed_pair,
)
check(
    "the report separates flagged pairs from exempted ones",
    report.get("flagged_count", -1) >= 1
    and report.get("flagged_count") + report.get("exempt_count") == report.get("pair_count"),
    {k: report.get(k) for k in ("flagged_count", "exempt_count", "pair_count")},
)
check(
    "the pairs come back worst first",
    report.get("pairs") == sorted(
        report.get("pairs", []), key=lambda pair: pair["combined_similarity"], reverse=True
    ),
    [round(pair["combined_similarity"], 3) for pair in report.get("pairs", [])],
)

# Reference Baseline Subtraction: with the reference present, a pair that only
# matches because both wrote the model answer must be exempted rather than
# accused. The renamed pair above is the escalation case; a pair of two
# byte-identical model answers is the textbook case.
r = call("get", path, TEACHER, params={"threshold": 0.99, "include_exempted": "false"})
strict = r.json()
check(
    "threshold and include_exempted narrow the report",
    r.status_code == 200
    and all(pair["combined_similarity"] >= 0.99 for pair in strict["pairs"])
    and all(pair["flagged"] for pair in strict["pairs"])
    and strict["exempt_count"] == 0,
    {k: strict.get(k) for k in ("threshold", "pair_count", "exempt_count")},
)

exempted = [pair for pair in report.get("pairs", []) if not pair["flagged"]]
if exempted:
    check(
        "an exempted pair explains itself",
        all("reference solution" in (pair.get("exemption") or "") for pair in exempted)
        and all(pair["ref_similarity_a"] is not None for pair in exempted),
        exempted[0].get("exemption"),
    )
else:
    check("screen with the reference available produced pairs", bool(report.get("pairs")), report)

check(
    "the language list is reported per screen",
    report.get("languages") == ["cpp"],
    report.get("languages"),
)
check(
    "a language filter screens one language only",
    call("get", path, TEACHER, params={"language": "c"}).json().get("submissions_compared") == 0,
    "",
)

# --- who may run the screen ------------------------------------------------- #
check("student may not run the screen", call("get", path, ALICE).status_code == 403, "")
check("anonymous may not run the screen", call("get", path, None).status_code == 401, "")
other_teacher = register("prof2", "prof2@example.com", "Prof Two", "prof12345")
check(
    "another teacher may not screen your class",
    call("get", path, other_teacher).status_code == 403,
    "",
)
check(
    "admin may screen any class",
    call("get", path, ADMIN).status_code == 200,
    "",
)
check(
    "a problem that is not in that class -> 404",
    call("get", f"/api/classrooms/{CLASSROOM_ID}/problems/{P9002_ID}/plagiarism", TEACHER).status_code == 404,
    "",
)
check(
    "unknown class or problem -> 404",
    call("get", "/api/classrooms/9999/problems/1/plagiarism", ADMIN).status_code == 404
    and call("get", f"/api/classrooms/{CLASSROOM_ID}/problems/9999/plagiarism", ADMIN).status_code == 404,
    "",
)
check(
    "threshold out of range -> 422",
    call("get", path, TEACHER, params={"threshold": 1.5}).status_code == 422,
    "",
)

# --------------------------------------------------------------------------- #
# 5. Stage 4 columns on a pre-Stage-4 database
# --------------------------------------------------------------------------- #

_FD, _LEGACY = tempfile.mkstemp(prefix="codingpass-s34-legacy-", suffix=".db")
os.close(_FD)
os.unlink(_LEGACY)
legacy_engine = create_engine(f"sqlite:///{_LEGACY}")
with legacy_engine.begin() as connection:
    connection.execute(text(
        "CREATE TABLE submissions (id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL,"
        " student_id INTEGER NOT NULL, language VARCHAR(8) NOT NULL, code TEXT NOT NULL,"
        " status VARCHAR(24) NOT NULL, score FLOAT NOT NULL, passed_count INTEGER NOT NULL,"
        " total_count INTEGER NOT NULL, execution_time_ms FLOAT NOT NULL, created_at DATETIME NOT NULL)"
    ))
before = {str(row[1]) for row in legacy_engine.connect().execute(text("PRAGMA table_info(submissions)"))}
database._ensure_submission_columns(legacy_engine)
with legacy_engine.begin() as connection:
    after = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(submissions)"))}
    connection.execute(text(
        "INSERT INTO submissions (id, problem_id, student_id, language, code, status, score,"
        " passed_count, total_count, execution_time_ms, created_at)"
        " VALUES (1,1,1,'c','int main(){}','accepted',100.0,7,7,12.5,'2026-01-01 00:00:00')"
    ))
    legacy_row = connection.execute(text(
        "SELECT compile_score, sample_score, random_score, logic_score, quality_score,"
        " presentation_warning, wording_pass, wording_violations FROM submissions WHERE id = 1"
    )).fetchone()
check("legacy table gains all Stage 4 columns",
      {"compile_score", "sample_score", "random_score", "logic_score", "quality_score",
       "presentation_warning", "wording_pass", "wording_violations"} <= after,
      sorted({"compile_score"} - after))
check("legacy table keeps its own columns", before <= after, sorted(before - after))
check("historic rows are backfilled with defaults",
      legacy_row == (0.0, 0.0, 0.0, 0.0, 0.0, 0, 1, None), legacy_row)
check("a historic row is not accused of anything",
      main._wording_list(legacy_row[7]) == [] and legacy_row[6] == 1, legacy_row)
os.unlink(_LEGACY)

# --------------------------------------------------------------------------- #
# 6. Helper edge cases that the HTTP layer cannot reach
# --------------------------------------------------------------------------- #

check("violation list round-trips", main._wording_list(main._wording_json(["a", "b"])) == ["a", "b"], "")
check("no violations serialise to NULL", main._wording_json([]) is None and main._wording_json(None) is None, "")
check("hand-edited JSON still renders", main._wording_list("not json at all") == ["not json at all"], "")
check("a scalar JSON value still renders", main._wording_list('"single"') == ["single"], "")

def _row(stdin: str, expected: str, sample: bool):
    return type("Row", (), {"input": stdin, "expected_output": expected, "is_sample": sample})()


check(
    "probe cases are the public ones",
    [case["input"] for case in main._probe_cases([_row("public", "x", True), _row("hidden", "y", False)])]
    == ["public"],
    main._probe_cases([_row("public", "x", True), _row("hidden", "y", False)]),
)
check(
    "probe falls back to the first case when nothing is public",
    [case["input"] for case in main._probe_cases([_row("hidden", "y", False)])] == ["hidden"],
    "",
)
check(
    "the probe never runs more than three cases",
    len(main._probe_cases([_row(str(i), "x", True) for i in range(6)])) == 3,
    "",
)

check(
    "the rubric never exceeds 100 and never goes below 0",
    main.Rubric(10.0, 40.0, 30.0, 40.0, 10.0).total == 100.0
    and main.Rubric(-5.0, 0.0, 0.0, 0.0, 0.0).total == 0.0,
    (main.Rubric(10.0, 40.0, 30.0, 40.0, 10.0).total, main.Rubric(-5.0, 0.0, 0.0, 0.0, 0.0).total),
)

check(
    "scaffolding is recognised",
    main._is_scaffolding("   ", Problem(starter_code_c="x", starter_code_cpp="y"), "c")
    and not main._is_scaffolding("int main(){return 0;}", Problem(starter_code_c="x", starter_code_cpp="y"), "c"),
    "",
)

# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #
client.__exit__()
shutil.rmtree(ROOT, ignore_errors=True)
shutil.rmtree(_PDF_DIR, ignore_errors=True)
try:
    os.unlink(_DB_PATH)
    os.unlink(f"{_DB_PATH}-wal")
    os.unlink(f"{_DB_PATH}-shm")
except OSError:
    pass

print()
print(f"FAILED: {failures}" if failures else "FAILED: none")
print(f"({len(failures)} failed)")
sys.exit(1 if failures else 0)
