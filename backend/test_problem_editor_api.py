"""Unit and integration tests for Problem & Test Case Visual Editor and Tags endpoints."""

import os
import sys
from fastapi.testclient import TestClient

# Ensure backend root is on path
sys.path.insert(0, os.path.dirname(__file__))

from main import app
from database import init_db
from models import Difficulty

client = TestClient(app)

def test_problem_editor_endpoints():
    # 1. Login as teacher (Prof. Chen)
    login_resp = client.post("/api/auth/login", json={"username": "teacher1", "password": "teacher123"})
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Login as student (Alice)
    student_resp = client.post("/api/auth/login", json={"username": "student1", "password": "student123"})
    assert student_resp.status_code == 200
    student_token = student_resp.json()["access_token"]
    student_headers = {"Authorization": f"Bearer {student_token}"}

    # 3. Create a problem with tags
    create_resp = client.post(
        "/api/problems",
        headers=headers,
        json={
            "title": "Topic Math & Loops Problem",
            "description": "# Title\nCalculate sum of multiples of 3.",
            "difficulty": "easy",
            "time_limit_sec": 1.5,
            "memory_limit_mb": 128,
            "tags": ["迴圈", "數學", "基礎語法"],
            "test_cases": [
                {"input": "10\n", "expected_output": "18\n", "is_sample": True},
                {"input": "100\n", "expected_output": "1683\n", "is_sample": False},
            ],
        },
    )
    assert create_resp.status_code == 201, f"Create failed: {create_resp.text}"
    created = create_resp.json()
    prob_id = created["id"]
    assert created["title"] == "Topic Math & Loops Problem"
    assert created["time_limit_sec"] == 1.5
    assert created["memory_limit_mb"] == 128
    assert "迴圈" in created["tags"]
    assert "數學" in created["tags"]
    print("PASS: Problem created with tags, limits, and cases successfully!")

    # 4. Student cannot update problem
    patch_student = client.patch(
        f"/api/problems/{prob_id}",
        headers=student_headers,
        json={"title": "Hacked Title"},
    )
    assert patch_student.status_code == 403
    print("PASS: Student prevented from modifying problem (403 Forbidden).")

    # 5. Teacher updates problem details and tags
    patch_resp = client.patch(
        f"/api/problems/{prob_id}",
        headers=headers,
        json={
            "title": "Topic Math & Loops (Updated)",
            "difficulty": "medium",
            "time_limit_sec": 2.5,
            "tags": ["迴圈", "數學", "動態規劃"],
        },
    )
    assert patch_resp.status_code == 200, f"Patch failed: {patch_resp.text}"
    updated = patch_resp.json()
    assert updated["title"] == "Topic Math & Loops (Updated)"
    assert updated["difficulty"] == "medium"
    assert updated["time_limit_sec"] == 2.5
    assert "動態規劃" in updated["tags"]
    print("PASS: Problem successfully updated via PATCH /api/problems/{id}!")

    # 6. Test tag filtering in list_problems
    list_tag_resp = client.get("/api/problems?tag=動態規劃", headers=headers)
    assert list_tag_resp.status_code == 200
    tag_probs = list_tag_resp.json()["problems"]
    assert any(p["id"] == prob_id for p in tag_probs), "Created problem not found by tag filter"
    print("PASS: GET /api/problems?tag=... filtering works accurately!")

    # 7. Teacher views all test cases (both sample and hidden)
    cases_resp = client.get(f"/api/problems/{prob_id}/testcases", headers=headers)
    assert cases_resp.status_code == 200
    cases = cases_resp.json()
    assert len(cases) == 2
    sample_case = next(c for c in cases if c["is_sample"])
    hidden_case = next(c for c in cases if not c["is_sample"])
    assert hidden_case["input"] == "100\n"
    print("PASS: Teacher can inspect all test cases including hidden data.")

    # 8. Student cannot inspect hidden test cases via /testcases
    student_cases_resp = client.get(f"/api/problems/{prob_id}/testcases", headers=student_headers)
    assert student_cases_resp.status_code == 403
    print("PASS: Student blocked from viewing all test cases (403 Forbidden).")

    # 9. Teacher updates a testcase
    put_case_resp = client.put(
        f"/api/problems/{prob_id}/testcases/{hidden_case['id']}",
        headers=headers,
        json={"input": "200\n", "expected_output": "6633\n"},
    )
    assert put_case_resp.status_code == 200
    updated_case = put_case_resp.json()
    assert updated_case["input"] == "200\n"
    assert updated_case["expected_output"] == "6633\n"
    print("PASS: PUT /api/problems/{id}/testcases/{case_id} updated testcase successfully.")

    # 10. Teacher deletes a testcase
    del_case_resp = client.delete(
        f"/api/problems/{prob_id}/testcases/{sample_case['id']}",
        headers=headers,
    )
    assert del_case_resp.status_code == 200
    cases_after_del = client.get(f"/api/problems/{prob_id}/testcases", headers=headers).json()
    assert len(cases_after_del) == 1
    print("PASS: DELETE /api/problems/{id}/testcases/{case_id} deleted testcase successfully.")

    # 11. Teacher deletes problem
    del_prob_resp = client.delete(f"/api/problems/{prob_id}", headers=headers)
    assert del_prob_resp.status_code == 200
    get_del = client.get(f"/api/problems/{prob_id}", headers=headers)
    assert get_del.status_code == 404
    print("PASS: DELETE /api/problems/{id} deleted problem completely.")

    print("\n=== ALL PROBLEM EDITOR & TAGS API TESTS PASSED! ===")

if __name__ == "__main__":
    init_db()
    test_problem_editor_endpoints()
