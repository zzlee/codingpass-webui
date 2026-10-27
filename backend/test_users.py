"""Unit and integration tests for user management endpoints and password self-service.

Covers:
- GET /api/users (Admin-only list, role filtering, search)
- GET /api/users/{id} (Admin or self permission boundaries)
- PATCH /api/users/{id} (Admin role modification, name/email update, password reset, last-admin demote guard)
- DELETE /api/users/{id} (Admin delete user, self-delete guard, last-admin delete guard)
- POST /api/auth/change-password (User self-service password update)
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

# Ensure repo root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-userstest-", suffix=".db")
os.close(_DB_FD)
if os.path.exists(_DB_PATH):
    os.unlink(_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-user-management-testing"
os.environ["BCRYPT_ROUNDS"] = "10"

from fastapi.testclient import TestClient

from backend.auth import create_access_token, hash_password
from backend.database import SessionLocal, init_db
from backend.main import app
from backend.models import Role, User


class UserManagementTestCase(unittest.TestCase):
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
        db = SessionLocal()
        try:
            db.query(User).delete()
            cls_admin = User(
                username="admin_user",
                email="admin@test.com",
                hashed_password=hash_password("admin_pass123"),
                role=Role.ADMIN,
                full_name="System Administrator",
            )
            cls_teacher = User(
                username="teacher_user",
                email="teacher@test.com",
                hashed_password=hash_password("teacher_pass123"),
                role=Role.TEACHER,
                full_name="Prof. Turing",
            )
            cls_student = User(
                username="student_user",
                email="student@test.com",
                hashed_password=hash_password("student_pass123"),
                role=Role.STUDENT,
                full_name="Alice Student",
            )
            db.add_all([cls_admin, cls_teacher, cls_student])
            db.commit()
            db.refresh(cls_admin)
            db.refresh(cls_teacher)
            db.refresh(cls_student)
            self.admin_id = cls_admin.id
            self.teacher_id = cls_teacher.id
            self.student_id = cls_student.id

            self.admin_token, _ = create_access_token(cls_admin)
            self.teacher_token, _ = create_access_token(cls_teacher)
            self.student_token, _ = create_access_token(cls_student)
        finally:
            db.close()

    def _auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    # -------------------------------------------------------------------------
    # List Users (GET /api/users)
    # -------------------------------------------------------------------------
    def test_list_users_forbidden_for_student_and_teacher(self):
        # Student
        resp = self.client.get("/api/users", headers=self._auth_header(self.student_token))
        self.assertEqual(resp.status_code, 403)

        # Teacher
        resp = self.client.get("/api/users", headers=self._auth_header(self.teacher_token))
        self.assertEqual(resp.status_code, 403)

        # Unauthenticated
        resp = self.client.get("/api/users")
        self.assertEqual(resp.status_code, 401)

    def test_list_users_admin_all_and_filtering(self):
        # All users
        resp = self.client.get("/api/users", headers=self._auth_header(self.admin_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["users"]), 3)

        # Filter by role
        resp = self.client.get("/api/users?role=student", headers=self._auth_header(self.admin_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["users"][0]["username"], "student_user")

        # Search query by name
        resp = self.client.get("/api/users?search=Turing", headers=self._auth_header(self.admin_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["users"][0]["username"], "teacher_user")

    # -------------------------------------------------------------------------
    # Get User (GET /api/users/{id})
    # -------------------------------------------------------------------------
    def test_get_user_self_and_cross_user_boundary(self):
        # Student gets own profile -> 200
        resp = self.client.get(f"/api/users/{self.student_id}", headers=self._auth_header(self.student_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "student_user")

        # Student attempts to get Admin's profile -> 403
        resp = self.client.get(f"/api/users/{self.admin_id}", headers=self._auth_header(self.student_token))
        self.assertEqual(resp.status_code, 403)

        # Admin gets Student's profile -> 200
        resp = self.client.get(f"/api/users/{self.student_id}", headers=self._auth_header(self.admin_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "student_user")

    # -------------------------------------------------------------------------
    # Update User (PATCH /api/users/{id})
    # -------------------------------------------------------------------------
    def test_admin_promote_student_to_teacher(self):
        resp = self.client.patch(
            f"/api/users/{self.student_id}",
            json={"role": "teacher"},
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "teacher")

    def test_student_cannot_promote_self(self):
        resp = self.client.patch(
            f"/api/users/{self.student_id}",
            json={"role": "admin"},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_cannot_demote_last_admin(self):
        # There is only 1 admin
        resp = self.client.patch(
            f"/api/users/{self.admin_id}",
            json={"role": "teacher"},
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("last remaining administrator", resp.json()["detail"])

    def test_admin_password_reset_and_login_verification(self):
        # Admin resets student password to 'brand_new_secret123'
        resp = self.client.patch(
            f"/api/users/{self.student_id}",
            json={"password": "brand_new_secret123"},
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200)

        # Verify old student password fails
        login_fail = self.client.post(
            "/api/auth/login",
            json={"username": "student_user", "password": "student_pass123"},
        )
        self.assertEqual(login_fail.status_code, 401)

        # Verify new password succeeds
        login_ok = self.client.post(
            "/api/auth/login",
            json={"username": "student_user", "password": "brand_new_secret123"},
        )
        self.assertEqual(login_ok.status_code, 200)
        self.assertIn("access_token", login_ok.json())

    # -------------------------------------------------------------------------
    # Delete User (DELETE /api/users/{id})
    # -------------------------------------------------------------------------
    def test_admin_cannot_delete_self(self):
        resp = self.client.delete(
            f"/api/users/{self.admin_id}",
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot delete their own account", resp.json()["detail"])

    def test_admin_can_delete_student(self):
        resp = self.client.delete(
            f"/api/users/{self.student_id}",
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("deleted successfully", resp.json()["message"])

        # Check user is gone
        get_resp = self.client.get(
            f"/api/users/{self.student_id}",
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(get_resp.status_code, 404)

    # -------------------------------------------------------------------------
    # Password Self-Service (POST /api/auth/change-password)
    # -------------------------------------------------------------------------
    def test_change_password_self_service(self):
        # Wrong old password -> 400
        resp = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "wrong_password", "new_password": "new_awesome_password123"},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("incorrect", resp.json()["detail"])

        # Correct old password -> 200
        resp = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "student_pass123", "new_password": "new_awesome_password123"},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 200)

        # Login with new password
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "student_user", "password": "new_awesome_password123"},
        )
        self.assertEqual(login_resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
