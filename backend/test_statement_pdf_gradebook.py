"""End-to-end tests for the statement-PDF stream and the gradebook CSV export.

Run: ``python test_statement_pdf_gradebook.py`` (no pytest). Uses a throwaway
SQLite file in ``/tmp``, so the real ``codingpass.db`` is never touched.

Covers:

* ``GET /api/problems/{id}/pdf`` - inline delivery of a pack statement PDF, the
  fact that its authorisation is exactly the problem's visibility rule, and the
  ``404`` shapes (no PDF attached, file deleted on disk, unknown problem).
* ``GET /api/classrooms/{id}/gradebook.csv`` - BOM, header layout, best attempt
  per problem with a teacher override, attempt counts, zeros for unattempted
  problems, and who may pull the file.
"""

from __future__ import annotations

import csv
import io
import os
import pathlib
import sys
import tempfile

# --- environment must be configured before main/database import ------------ #
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-pdf-gb-", suffix=".db")
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "statement-pdf-test-secret-0123456789-abc"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ.setdefault("CODE_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import Problem  # noqa: E402

client = TestClient(main.app)
client.__enter__()  # lifespan: create tables + seed the demo dataset
failures: list[str] = []

_WORK_DIR = pathlib.Path(tempfile.mkdtemp(prefix="codingpass-pdf-gb-files-"))
_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def sign_in(username: str, password: str) -> dict:
    reply = client.post("/api/auth/login", json={"username": username, "password": password})
    assert reply.status_code == 200, reply.text
    return {"Authorization": f"Bearer {reply.json()['access_token']}"}


def pack_label(problem: dict) -> str:
    """The column prefix the server uses: ``problem_code`` or ``P<id>``."""
    return problem["problem_code"] or f"P{problem['id']}"


def attach_pdf(problem_id: int, path: str) -> None:
    """Write ``pdf_path`` directly: only the pack importer ever sets it."""
    session = SessionLocal()
    try:
        session.get(Problem, problem_id).pdf_path = path
        session.commit()
    finally:
        session.close()


ADMIN = sign_in("admin", "admin123")
TEACHER = sign_in("teacher1", "teacher123")
ALICE = sign_in("student1", "student123")  # enrolled in the demo class CS101
BOB = sign_in("student2", "student123")  # not enrolled anywhere

# --- a problem of the demo class, with a PDF attached ----------------------- #
reply = client.post(
    "/api/problems",
    headers=TEACHER,
    json={
        "title": "Statement PDF Probe",
        "description": "Graded on one sample case.",
        "classroom_id": 1,
        "test_cases": [{"input": "1 2\n", "expected_output": "3\n", "is_sample": True}],
    },
)
check("teacher created the probe problem", reply.status_code == 201, reply.text[:160])
PROBLEM_ID = reply.json()["id"]

reply = client.post(
    "/api/auth/register",
    headers=ADMIN,
    json={
        "username": "teacher2",
        "email": "teacher2@codingpass.local",
        "password": "teacher123",
        "full_name": "Prof. Two",
        "role": "teacher",
    },
)
check("admin created a second teacher", reply.status_code == 201, reply.text[:160])
OTHER_TEACHER = sign_in("teacher2", "teacher123")

# --- GET /api/problems/{id}/pdf --------------------------------------------- #
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=TEACHER)
check(
    "no PDF attached -> 404 with a reason",
    reply.status_code == 404 and "statement PDF" in reply.text,
    reply.text[:120],
)

PDF_PATH = _WORK_DIR / "statement.pdf"
PDF_PATH.write_bytes(_MINIMAL_PDF)
attach_pdf(PROBLEM_ID, str(PDF_PATH))

reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=TEACHER)
disposition = reply.headers.get("content-disposition", "")
check(
    "owner gets the PDF inline",
    reply.status_code == 200
    and reply.headers.get("content-type") == "application/pdf"
    and reply.content.startswith(b"%PDF-1.4"),
    (reply.status_code, reply.headers.get("content-type")),
)
check(
    "PDF is named and inline, not forced as a download",
    "inline" in disposition and f"problem_{PROBLEM_ID}.pdf" in disposition,
    disposition,
)
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=ALICE)
check("enrolled student may read the statement", reply.status_code == 200, reply.status_code)
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=BOB)
check("student outside the class -> 403", reply.status_code == 403, reply.status_code)
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf")
check("anonymous -> 401", reply.status_code == 401, reply.status_code)
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=ADMIN)
check("admin may read the statement", reply.status_code == 200, reply.status_code)

# The path comes from the database, not the request: an outsider's problem stays
# closed even though the PDF itself is a readable file on disk.
reply = client.post(
    "/api/problems",
    headers=OTHER_TEACHER,
    json={"title": "Private Bank Statement", "test_cases": [{"input": "", "expected_output": ""}]},
)
PRIVATE_ID = reply.json()["id"]
attach_pdf(PRIVATE_ID, str(PDF_PATH))
reply = client.get(f"/api/problems/{PRIVATE_ID}/pdf", headers=ALICE)
check("another teacher's private problem -> 403", reply.status_code == 403, reply.status_code)
reply = client.get("/api/problems/999999/pdf", headers=ADMIN)
check("unknown problem -> 404", reply.status_code == 404, reply.status_code)

# A pack whose PDF was moved on disk is a 404, never a 500 or a path disclosure.
VANISHED = _WORK_DIR / "deleted.pdf"
VANISHED.write_bytes(_MINIMAL_PDF)
attach_pdf(PROBLEM_ID, str(VANISHED))
VANISHED.unlink()
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=TEACHER)
check(
    "PDF deleted from disk -> 404",
    reply.status_code == 404 and str(VANISHED) not in reply.text,
    reply.text[:120],
)

# --- GET /api/classrooms/{id}/gradebook.csv --------------------------------- #
CLASS_ID = 1  # the seeded CS101 class, taught by teacher1
CLASS_CODE = "CS101"

# Column order is ascending problem id; the whole class grid has to line up.
problems = sorted(
    client.get("/api/problems?classroom_id=1", headers=TEACHER).json()["problems"],
    key=lambda row: row["id"],
)
check("the demo class has problems to grade", len(problems) >= 2, [row["title"] for row in problems])

ACCEPT = (
    "#include <stdio.h>\n"
    'int main(void){int a,b;if(scanf("%d %d",&a,&b)!=2)return 1;'
    'printf("%d\\n",a+b);return 0;}\n'
)
WRONG = '#include <stdio.h>\nint main(void){printf("9\\n");return 0;}\n'
target = problems[0]["id"]

reply = client.post(
    f"/api/problems/{target}/submit", headers=ALICE, json={"language": "c", "code": ACCEPT}
)
check("first attempt recorded", reply.status_code == 200 and reply.json()["submission_id"], reply.text[:160])
best_id, judge_score = reply.json()["submission_id"], reply.json()["score"]
reply = client.post(
    f"/api/problems/{target}/submit", headers=ALICE, json={"language": "c", "code": WRONG}
)
check("second attempt recorded", reply.status_code == 200, reply.text[:160])
check("the two attempts scored differently", judge_score > reply.json()["score"], judge_score)

# A teacher mark replaces the judge's verdict for *that* attempt, which is then
# the number the gradebook reports even though the judge had said 100.
reply = client.patch(
    f"/api/submissions/{best_id}/review",
    headers=TEACHER,
    json={"teacher_score": 12.5, "teacher_feedback": "Output format"},
)
check("teacher graded the attempt", reply.status_code == 200, reply.text[:160])

reply = client.get(f"/api/classrooms/{CLASS_ID}/gradebook.csv", headers=TEACHER)
check(
    "CSV answers 200 as text/csv",
    reply.status_code == 200 and reply.headers.get("content-type", "").startswith("text/csv"),
    reply.headers.get("content-type"),
)
check(
    "CSV downloads under the class code",
    f"gradebook_{CLASS_CODE}.csv" in reply.headers.get("content-disposition", ""),
    reply.headers.get("content-disposition"),
)
check("CSV starts with a UTF-8 BOM for Excel", reply.content.startswith(b"\xef\xbb\xbf"), reply.content[:3])

rows = list(csv.reader(io.StringIO(reply.content.decode("utf-8-sig"))))
header, alice = rows[0], next(row for row in rows[1:] if row[1] == "student1")
check("identity columns come first", header[:4] == ["student_id", "username", "full_name", "email"], header[:4])
check("totals come last", header[-2:] == ["average_score", "total_score"], header[-2:])
check(
    "one score + one attempts column per problem, in id order",
    header[4:-2:2] == [f"{pack_label(row)}_{row['title']}_score" for row in problems]
    and header[5:-2:2] == [f"{pack_label(row)}_attempts" for row in problems],
    header[4:-2],
)

score_column = 4 + 2 * [row["id"] for row in problems].index(target)
check("teacher override wins over the judge score", alice[score_column] == "12.5", alice[score_column])
check("every attempt is counted", alice[score_column + 1] == "2", alice[score_column + 1])
check(
    "unattempted problems are zeros, not blanks",
    all(
        alice[column] == "0.0" and alice[column + 1] == "0"
        for column in range(4, len(alice) - 2, 2)
        if column != score_column
    ),
    alice,
)
check("total_score sums the best scores", alice[-1] == "12.5", alice[-1])
check(
    "average_score divides the total by the problem count",
    alice[-2] == str(round(12.5 / len(problems), 2)),
    (alice[-2], len(problems)),
)
check("only enrolled students appear", [row[1] for row in rows[1:]] == ["student1"], [row[1] for row in rows[1:]])

reply = client.get(f"/api/classrooms/{CLASS_ID}/gradebook.csv", headers=ADMIN)
check("admin may export any class", reply.status_code == 200 and reply.content.startswith(b"\xef\xbb\xbf"), reply.status_code)
reply = client.get(f"/api/classrooms/{CLASS_ID}/gradebook.csv", headers=OTHER_TEACHER)
check("another teacher -> 403", reply.status_code == 403, reply.text[:120])
reply = client.get(f"/api/classrooms/{CLASS_ID}/gradebook.csv", headers=ALICE)
check("student -> 403", reply.status_code == 403, reply.status_code)
reply = client.get(f"/api/classrooms/{CLASS_ID}/gradebook.csv")
check("anonymous -> 401", reply.status_code == 401, reply.status_code)
reply = client.get("/api/classrooms/999999/gradebook.csv", headers=ADMIN)
check("unknown class -> 404", reply.status_code == 404, reply.status_code)

reply = client.post(
    "/api/classrooms", headers=TEACHER, json={"name": "Nobody Here", "code": "EMPTY42"}
)
empty_id = reply.json()["id"]
reply = client.get(f"/api/classrooms/{empty_id}/gradebook.csv", headers=TEACHER)
lines = [line for line in reply.content.decode("utf-8-sig").splitlines() if line.strip()]
check(
    "an empty class exports a header row only",
    reply.status_code == 200 and lines == ["student_id,username,full_name,email,average_score,total_score"],
    lines,
)

# --- statement PDF hardening: symlinks, response headers, path disclosure --- #
# ``pdf_path`` is a database value chosen by the pack importer, and a pack folder
# is a path a teacher picks - so a ``statement.pdf`` that is really a symlink must
# never turn this endpoint into an arbitrary file read.
SECRET_FILE = _WORK_DIR / "server-secret.pdf"
SECRET_FILE.write_bytes(b"%PDF-1.4\nSECRET-CONTENT-DATA\n%%EOF\n")
LINK_PATH = _WORK_DIR / "statement-link.pdf"
try:
    LINK_PATH.symlink_to(SECRET_FILE)
    have_symlinks = True
except OSError:  # pragma: no cover - filesystem without symlinks
    have_symlinks = False

if have_symlinks:
    attach_pdf(PROBLEM_ID, str(LINK_PATH))
    reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=ALICE)
    check(
        "statement PDF that is a symlink -> 404, target never served",
        reply.status_code == 404 and b"SECRET-CONTENT-DATA" not in reply.content,
        (reply.status_code, reply.content[:40]),
    )

attach_pdf(PROBLEM_ID, str(PDF_PATH))
reply = client.get(f"/api/problems/{PROBLEM_ID}/pdf", headers=ALICE)
check(
    "PDF response is nosniff, CSP-sandboxed and uncacheable",
    reply.status_code == 200
    and reply.headers.get("x-content-type-options") == "nosniff"
    and "sandbox" in (reply.headers.get("content-security-policy") or "")
    and "no-store" in (reply.headers.get("cache-control") or ""),
    {
        key: reply.headers.get(key)
        for key in ("x-content-type-options", "content-security-policy", "cache-control")
    },
)

student_detail = client.get(f"/api/problems/{PROBLEM_ID}", headers=ALICE).json()
check(
    "student payload no longer leaks the server filesystem path",
    not student_detail.get("pdf_path"),
    student_detail.get("pdf_path"),
)
staff_detail = client.get(f"/api/problems/{PROBLEM_ID}", headers=TEACHER).json()
check(
    "the problem's own teacher still sees the path",
    staff_detail.get("pdf_path") == str(PDF_PATH),
    staff_detail.get("pdf_path"),
)

# --- CSV injection: a name or title must not run as a spreadsheet formula --- #
reply = client.post(
    "/api/auth/register",
    json={
        "username": "eve",
        "password": "eve12345",
        "email": "eve@codingpass.local",
        "full_name": "=cmd|'/C calc'!A0",
        "role": "student",
    },
)
check("registered a student whose name is a formula payload", reply.status_code == 201, reply.text[:160])
EVE = sign_in("eve", "eve12345")
reply = client.post(
    "/api/classrooms", headers=OTHER_TEACHER, json={"name": "Injection Lab", "code": "INJECT1"}
)
inj_id, inj_code = reply.json()["id"], reply.json()["code"]
reply = client.post("/api/classrooms/join", headers=EVE, json={"code": inj_code})
check("that student joins the class", reply.status_code in (200, 201), reply.text[:160])
reply = client.post(
    "/api/problems",
    headers=OTHER_TEACHER,
    json={
        "title": '=HYPERLINK("http://evil.example")',
        "classroom_id": inj_id,
        "test_cases": [{"input": "", "expected_output": ""}],
    },
)
check("a formula-shaped problem title is accepted", reply.status_code == 201, reply.text[:160])

reply = client.get(f"/api/classrooms/{inj_id}/gradebook.csv", headers=OTHER_TEACHER)
sheet = reply.content.decode("utf-8-sig")
check(
    "gradebook is not cacheable (it is a roster of marks and e-mails)",
    "no-store" in (reply.headers.get("cache-control") or "")
    and reply.headers.get("x-content-type-options") == "nosniff",
    {
        key: reply.headers.get(key)
        for key in ("cache-control", "x-content-type-options")
    },
)
check(
    "formula-shaped student name is neutralised",
    "'=cmd" in sheet and ",=cmd" not in sheet,
    sheet.splitlines()[-1] if sheet.splitlines() else sheet,
)
import csv as _csv
import io as _io

header_cells = next(_csv.reader(_io.StringIO(sheet.splitlines()[0])))
check(
    "the formula-shaped title is still a *label*, not a formula",
    any("=HYPERLINK" in cell and not cell.startswith(("=", "+", "-", "@")) for cell in header_cells),
    [cell for cell in header_cells if "HYPERLINK" in cell],
)
check(
    "no header cell can start a spreadsheet formula",
    not any(cell[:1] in ("=", "+", "-", "@", "\t") for cell in header_cells),
    [cell for cell in header_cells if cell[:1] in ("=", "+", "-", "@", "\t")],
)

client.__exit__(None, None, None)
print()
print("FAILED:", failures if failures else "none")
print(f"({len(failures)} failed; db kept at {_DB_PATH})")
sys.exit(1 if failures else 0)
