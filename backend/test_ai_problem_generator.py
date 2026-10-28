"""Smoke tests for the AI problem generator (no pytest, no live vLLM).

Run from ``backend/``: ``python test_ai_problem_generator.py``.

The vLLM transport is monkeypatched, while the *real* toolchain (g++ + node)
performs the dual-run validation when it is available; without it the
sandbox-dependent assertions are skipped and reported.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services import ai_problem_generator as gen

failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(label)


REF = """\
#include <iostream>
using namespace std;
int main() {
    long long a, b;
    while (cin >> a >> b) {
        if (a > b) { long long t = a; a = b; b = t; }
        long long s = 0;
        for (long long i = a; i <= b; i++) s += i * i;
        cout << s << "\\n";
    }
    return 0;
}
"""

TEDA = """\
[30~50;enn]{
  @A = 1~1000;
  @B = 1~1000;
  P(0.2){ @A = 1; @B = 1; }
  P(0.4){ @A = 200~300; }
  P(0.4){ @B = 999~1000; }
  A + " " + B;
};
"""

ANSWER = {
    "problem_code": "",
    "title": "平方和測試",
    "description": "給定區間 [A,B]，計算該區間內所有整數的平方和。\n輸入多筆直到 EOF。",
    "input_format": "每行兩個整數 A、B（1 <= A,B <= 1000），讀到檔案結束為止。",
    "output_format": "每行輸出對應測資的平方和。",
    "time_limit_sec": 3.0,
    "memory_limit_mb": 256,
    "starter_code_cpp": "",
    "starter_code_c": "",
    "ref_code": REF,
    "teda_random_script": TEDA,
    "wording_rules": "#term[0;skipComments skipStrings] system",
    "teda_scoring_script": "",
    "sample_cases": [
        {"input": "1 3\n", "output": "14\n", "explanation": "1+4+9=14"},
        {"input": "2 2\n", "output": "4\n", "explanation": "單位區間邊界"},
        {"input": "5 1\n", "output": "55\n", "explanation": "反向區間自動調換"},
    ],
    "sample_file_text": "",
}


def sandbox_available() -> bool:
    return shutil.which("g++") is not None and shutil.which("node") is not None


def make_db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    teacher = models.User(username="t1", email="t@x.io", hashed_password="x", role="teacher")
    db.add(teacher)
    db.commit()
    return db, teacher


# --------------------------------------------------------------------------- #
# 1. JSON recovery: fenced/chatty answer with a raw newline + trailing comma.
# --------------------------------------------------------------------------- #
blob = json.dumps(ANSWER, ensure_ascii=False)
blob = blob.replace("計算該區間內所有整數的平方和。", "計算該區間內所有整數的\n平方和。")
blob = blob.rstrip("}") + ",\n}"
parsed = gen._parse_model_json("好的：\n```json\n" + blob + "\n```\n請查收。")
spec = gen._build_spec(
    parsed,
    requested_code=None,
    code_assigned=False,
    fallback_code="P1001",
    requested_difficulty="",
    warnings=[],
)
check("json recovery: title", spec["title"] == "平方和測試", spec["title"])
check("json recovery: fallback code", spec["problem_code"] == "P1001")
check("json recovery: 3 samples", len(spec["sample_cases"]) == 3)
check(
    "json recovery: wording baseline merged",
    "#term[0] <ctime>" in spec["wording_rules"] and "system" in spec["wording_rules"],
)

# --------------------------------------------------------------------------- #
# 2. Problem-code auto assignment from workspace folder names.
# --------------------------------------------------------------------------- #
ws = pathlib.Path(tempfile.mkdtemp(prefix="test-aigen-"))
(ws / "P1204_萬年月曆").mkdir()
(ws / "P1205_區間質數.zip").touch()
check("next code", gen.next_problem_code(None, ws) == "P1206")

# --------------------------------------------------------------------------- #
# 3. Sample file text: separated 【範例輸入】/【範例輸出】 blocks, and the
#    offline parser recovers the joined block back.
# --------------------------------------------------------------------------- #
text = gen.build_sample_file_text(
    [{"input": "1 3", "output": "14"}, {"input": "2 2", "output": "4"}]
)
check("sample text separated blocks", text.index("【範例輸入】") < text.index("【範例輸出】"))
from problem_pack import parse_sample_text  # noqa: E402

pairs = parse_sample_text(text)
check("sample text re-parses", len(pairs) == 1 and "1 3" in pairs[0][0] and "14" in pairs[0][1])

# --------------------------------------------------------------------------- #
# 4. End-to-end with mocked transport + DB import (needs g++ + node).
# --------------------------------------------------------------------------- #
calls = {"n": 0}


def flaky_chat(messages):
    calls["n"] += 1
    if calls["n"] == 1:
        return "抱歉，我無法回答。"  # contract failure -> retry feedback path
    return "```json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n```"


if not sandbox_available():
    print("SKIP  end-to-end (g++/node not available on this host)")
else:
    gen._chat_completion = flaky_chat
    db, teacher = make_db()
    result = gen.generate_problem(
        "請出一道區間平方和的簡單題目",
        db=db,
        teacher=teacher,
        topic="數學",
        difficulty="easy",
        workspace_dir=ws,
        num_random=3,
        seed=42,
        make_pdf=False,
    )
    v = result["verification"]
    check("retry recovered on 2nd attempt", calls["n"] == 2, calls["n"])
    check("dual_run_verified", v["dual_run_verified"] is True, v)
    check("samples 3/3", v["samples_passed"] == 3 and v["samples_corrected"] == 0)
    check("hidden cases generated", v["hidden_case_count"] == 3, v["hidden_case_count"])
    check("problem stored", result["problem_id"] is not None, result["problem_id"])

    problem = db.get(models.Problem, result["problem_id"])
    check("db samples/hidden", len(problem.sample_test_cases) == 3 and len(problem.hidden_test_cases) == 3)
    check("db pack flags", problem.has_ref_code and problem.has_random_generator and problem.has_wording_rules)
    check("db ref_language cpp", problem.ref_language == "cpp")

    pack = pathlib.Path(result["pack_dir"])
    names = {p.name for p in pack.iterdir()}
    code = result["problem_code"]
    check("code auto-assigned past workspace numbers", code == "P1206", code)
    for suffix in ("庫題卡碼文標碼.cpp", "庫題卡測資０隨輸入.teda", "庫題卡碼文限碼.txt", "庫題卡測資範例.txt"):
        check(f"pack has {code}_{suffix}", any(n.endswith(suffix) and n.startswith(f"{code}_") for n in names), sorted(names))
    check("pack html present", any(n.endswith(".html") for n in names))
    check("zip present", pathlib.Path(result["zip_path"]).is_file())

    # The offline importer must auto-discover the freshly generated zip.
    import problem_pack  # noqa: E402

    imported = problem_pack.parse_problem_pack(result["zip_path"], generate_hidden=False)
    check("zip re-imports as a pack", imported["problem_code"] == code and imported["ref_code"], imported["warnings"])

    # ---- mismatched sample auto-corrects and vetoes dual_run_verified ---- #
    calls["n"] = 0
    broken = json.loads(json.dumps(ANSWER))
    broken["problem_code"] = "P2222"
    broken["sample_cases"][0]["output"] = "999\n"
    gen._chat_completion = lambda messages: json.dumps(broken, ensure_ascii=False)
    r2 = gen.generate_problem("x", workspace_dir=ws, num_random=1, auto_import=False, make_pdf=False)
    check("wrong sample corrected", r2["verification"]["samples_corrected"] == 1)
    check("wrong sample vetoes verified", r2["verification"]["dual_run_verified"] is False)
    check("corrected expected output", r2["sample_test_cases"][0]["expected_output"] == "14\n")

    # ---- uncompilable reference degrades gracefully ---- #
    broken2 = json.loads(json.dumps(ANSWER))
    broken2["problem_code"] = "P3333"
    broken2["ref_code"] = "int main( { syntax error"
    gen._chat_completion = lambda messages: json.dumps(broken2, ensure_ascii=False)
    r3 = gen.generate_problem("x", workspace_dir=ws, num_random=1, auto_import=False, make_pdf=False)
    check("bad ref not verified", r3["verification"]["dual_run_verified"] is False)
    check("bad ref files still shipped", pathlib.Path(r3["paths"]["reference_cpp"]).is_file())

# --------------------------------------------------------------------------- #
# 5. Guard rails.
# --------------------------------------------------------------------------- #
for label, kwargs in (
    ("empty prompt", {}),
    ("bad problem_code", {"problem_code": "1234"}),
):
    try:
        gen.generate_problem("   " if not kwargs else "x", workspace_dir=ws, **kwargs)
        check(f"guard {label} raises", False, "no exception")
    except gen.AIProblemGenerationError:
        check(f"guard {label} raises", True)

shutil.rmtree(ws, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("0 FAILED")
