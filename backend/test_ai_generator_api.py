"""Test AI Problem Generator API endpoints."""

from __future__ import annotations

import os
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-aigentest-", suffix=".db")
os.close(_DB_FD)
if os.path.exists(_DB_PATH):
    os.unlink(_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-ai-gen-testing"
os.environ["BCRYPT_ROUNDS"] = "10"

import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.database import init_db
from backend.main import app


class AIProblemGeneratorAPITestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        for p in (_DB_PATH, f"{_DB_PATH}-wal", f"{_DB_PATH}-shm"):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def setUp(self):
        # Login with seeded accounts
        t_res = self.client.post("/api/auth/login", json={"username": "teacher1", "password": "teacher123"})
        self.assertEqual(t_res.status_code, 200, f"Teacher login failed: {t_res.text}")
        self.teacher_token = t_res.json()["access_token"]

        s_res = self.client.post("/api/auth/login", json={"username": "student1", "password": "student123"})
        self.assertEqual(s_res.status_code, 200, f"Student login failed: {s_res.text}")
        self.student_token = s_res.json()["access_token"]

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_student_forbidden(self):
        resp = self.client.post(
            "/api/ai/generate-problem",
            json={"prompt": "出一個題目"},
            headers=self._auth(self.student_token),
        )
        self.assertEqual(resp.status_code, 403)

    @patch("backend.routers.ai.generate_problem")
    def test_teacher_generate_problem_success(self, mock_gen):
        mock_gen.return_value = {
            "problem_code": "P1299",
            "title": "質數區間和",
            "difficulty": "medium",
            "time_limit_sec": 2.0,
            "memory_limit_mb": 256,
            "description": "計算區間內的質數和",
            "input_format": "兩正整數 L R",
            "output_format": "輸出總和",
            "starter_code_c": "",
            "starter_code_cpp": "",
            "ref_code": "#include <iostream>...",
            "teda_random_script": "[30~50;enn]...",
            "wording_rules": "#term[0] system",
            "pack_dir": "/tmp/CodingPass/P1299_質數區間和",
            "zip_path": "/tmp/CodingPass/P1299_質數區間和.zip",
            "problem_id": 999,
            "sample_cases": [{"input": "1 10\n", "output": "17\n"}],
            "hidden_test_cases": [{"input": "2 5\n", "output": "10\n"}],
            "verification": {"dual_run_verified": True},
            "warnings": [],
        }

        resp = self.client.post(
            "/api/ai/generate-problem",
            json={"prompt": "出一個計算質數區間和的題目，難度中等", "difficulty": "medium"},
            headers=self._auth(self.teacher_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["problem_code"], "P1299")
        self.assertEqual(data["title"], "質數區間和")
        self.assertEqual(data["problem_id"], 999)
        self.assertTrue(data["dual_run_verified"])
        self.assertEqual(data["zip_download_url"], "/api/ai/packs/P1299/download")

    def test_list_packs_teacher(self):
        resp = self.client.get(
            "/api/ai/packs",
            headers=self._auth(self.teacher_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)


if __name__ == "__main__":
    unittest.main()
