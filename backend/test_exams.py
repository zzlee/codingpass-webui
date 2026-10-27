"""Unit and integration tests for Exam Mode and Live Anti-Cheat Proctoring.

Covers:
- POST /api/exams/{problem_id}/start (Session creation, resumption, permission guards, agent lock requirement)
- POST /api/exams/{session_id}/heartbeat (Telemetry, fullscreen exit tracking, blur counter, violation log)
- GET /api/exams/{problem_id}/monitor (Teacher live proctoring dashboard, student access forbidden)
- POST /api/exams/{session_id}/force-submit (Teacher force submit, student access forbidden)
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="codingpass-examstest-", suffix=".db")
os.close(_DB_FD)
if os.path.exists(_DB_PATH):
    os.unlink(_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-exam-mode-testing"
os.environ["BCRYPT_ROUNDS"] = "10"

from fastapi.testclient import TestClient

from backend.auth import create_access_token, exam_agent_token, hash_password
from backend.database import SessionLocal, init_db
from backend.main import app
from backend.models import Classroom, ClassroomMembership, ExamSession, Problem, Role, User


class ExamModeTestCase(unittest.TestCase):
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
            db.query(ExamSession).delete()
            db.query(ClassroomMembership).delete()
            db.query(Problem).delete()
            db.query(Classroom).delete()
            db.query(User).delete()

            self.teacher = User(
                username="prof_chen",
                email="chen@test.com",
                hashed_password=hash_password("teacher123"),
                role=Role.TEACHER,
                full_name="Prof. Chen",
            )
            self.student = User(
                username="alice",
                email="alice@test.com",
                hashed_password=hash_password("student123"),
                role=Role.STUDENT,
                full_name="Alice Student",
            )
            self.outsider = User(
                username="bob",
                email="bob@test.com",
                hashed_password=hash_password("student123"),
                role=Role.STUDENT,
                full_name="Bob Outsider",
            )
            db.add_all([self.teacher, self.student, self.outsider])
            db.commit()
            db.refresh(self.teacher)
            db.refresh(self.student)
            db.refresh(self.outsider)

            # Create classroom
            self.classroom = Classroom(
                name="CS101 Exam Hall",
                code="EXAM101",
                teacher_id=self.teacher.id,
            )
            db.add(self.classroom)
            db.commit()
            db.refresh(self.classroom)

            # Enrol alice only
            membership = ClassroomMembership(
                classroom_id=self.classroom.id,
                student_id=self.student.id,
            )
            db.add(membership)

            # Create standard exam problem
            self.problem = Problem(
                title="Midterm Exam Problem 1",
                description="Write an algorithm.",
                teacher_id=self.teacher.id,
                classroom_id=self.classroom.id,
                is_exam=True,
                exam_lock_required=False,
                max_blur_count=2,
            )
            # Create strict agent-lock exam problem
            self.strict_problem = Problem(
                title="Final Exam Strict Problem",
                description="Requires local agent kiosk.",
                teacher_id=self.teacher.id,
                classroom_id=self.classroom.id,
                is_exam=True,
                exam_lock_required=True,
                max_blur_count=1,
            )
            # Regular (non-exam) practice problem: exam endpoints must reject it
            self.plain_problem = Problem(
                title="Weekly Practice Exercise",
                description="Not an exam.",
                teacher_id=self.teacher.id,
                classroom_id=self.classroom.id,
                is_exam=False,
            )
            db.add_all([self.problem, self.strict_problem, self.plain_problem])
            db.commit()
            db.refresh(self.problem)
            db.refresh(self.strict_problem)
            db.refresh(self.plain_problem)

            self.teacher_token, _ = create_access_token(self.teacher)
            self.student_token, _ = create_access_token(self.student)
            self.outsider_token, _ = create_access_token(self.outsider)
        finally:
            db.close()

    def _auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_start_exam_session_success_and_resumption(self):
        # Alice starts exam session
        resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["problem_id"], self.problem.id)
        self.assertEqual(data["student_id"], self.student.id)
        self.assertEqual(data["status"], "in_progress")
        session_id = data["id"]

        # Resuming returns the exact same session id
        resume_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resume_resp.status_code, 200)
        self.assertEqual(resume_resp.json()["id"], session_id)

    def test_start_exam_strict_lock_required(self):
        # Alice tries starting strict problem without agent lock token -> 400
        resp = self.client.post(
            f"/api/exams/{self.strict_problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Local Agent", resp.json()["detail"])

        # Static / invented token strings no longer count as a verified lock
        resp_bad = self.client.post(
            f"/api/exams/{self.strict_problem.id}/start",
            json={"agent_lock_token": "local-agent-lock-active"},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp_bad.status_code, 400)

        # A genuine HMAC replayed for the wrong problem pair -> still 400
        wrong_pair_token = exam_agent_token(self.student.id, self.problem.id)
        resp_wrong = self.client.post(
            f"/api/exams/{self.strict_problem.id}/start",
            json={"agent_lock_token": wrong_pair_token},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp_wrong.status_code, 400)

        # Valid HMAC-SHA256 token bound to (student, strict problem) -> 200 OK
        good_token = exam_agent_token(self.student.id, self.strict_problem.id)
        resp_ok = self.client.post(
            f"/api/exams/{self.strict_problem.id}/start",
            json={"agent_lock_token": good_token},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertTrue(resp_ok.json()["agent_lock_verified"])

    def test_start_exam_rejects_non_exam_problem(self):
        # C3: exam endpoints only apply to problems flagged is_exam
        resp = self.client.post(
            f"/api/exams/{self.plain_problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not configured as an exam", resp.json()["detail"])

    def test_start_exam_after_completion_conflicts(self):
        # C3: a submitted/terminated session is final - never resurrected
        start_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        session_id = start_resp.json()["id"]

        force_resp = self.client.post(
            f"/api/exams/{session_id}/force-submit",
            headers=self._auth_header(self.teacher_token),
        )
        self.assertEqual(force_resp.status_code, 200)

        # Restarting the exam must 409, not silently wipe proctoring history
        again = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(again.status_code, 409)
        self.assertIn("completed or terminated", again.json()["detail"])

        # M7: telemetry beats on a closed session are rejected with 400
        hb = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={"is_fullscreen": True},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(hb.status_code, 400)

    def test_start_exam_outsider_forbidden(self):
        # Bob is not in CS101 classroom -> 403 Forbidden
        resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.outsider_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_exam_heartbeat_telemetry_and_warning(self):
        # Start session
        start_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        session_id = start_resp.json()["id"]

        # Send normal heartbeat (fullscreen, no blur)
        hb1 = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={"is_fullscreen": True, "blur_increment": 0},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(hb1.status_code, 200)
        self.assertEqual(hb1.json()["blur_count"], 0)
        self.assertIsNone(hb1.json()["warning"])

        # Send heartbeat indicating student left fullscreen & blurred window 3 times
        hb2 = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={
                "is_fullscreen": False,
                "blur_increment": 3,
                "violation_event": "ALT_TAB_SWITCH",
            },
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(hb2.status_code, 200)
        self.assertEqual(hb2.json()["fullscreen_exited_count"], 1)
        self.assertEqual(hb2.json()["blur_count"], 3)
        # Max blur count on problem is 2, so 3 should generate warning
        self.assertIsNotNone(hb2.json()["warning"])
        self.assertIn("exceeding maximum limit", hb2.json()["warning"])

        # Outsider cannot tamper with Alice's heartbeat
        tamper = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={"is_fullscreen": True, "blur_increment": 0},
            headers=self._auth_header(self.outsider_token),
        )
        self.assertEqual(tamper.status_code, 403)

    def test_heartbeat_rejects_out_of_bounds_fields(self):
        # M6: every telemetry field is range-constrained
        start_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        session_id = start_resp.json()["id"]

        for bad_payload in (
            {"is_fullscreen": True, "blur_increment": 11},
            {"is_fullscreen": True, "blur_increment": -1},
            {"is_fullscreen": True, "fullscreen_exited_increment": 11},
            {"is_fullscreen": True, "agent_blocked_count": -1},
            {"is_fullscreen": True, "violation_event": "X" * 65},
        ):
            resp = self.client.post(
                f"/api/exams/{session_id}/heartbeat",
                json=bad_payload,
                headers=self._auth_header(self.student_token),
            )
            self.assertEqual(resp.status_code, 422, bad_payload)

    def test_heartbeat_records_agent_blocked_count(self):
        # M6: agent hook telemetry persists (monotonic maximum) and the
        # explicit fullscreen_exited_increment is honoured.
        start_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        session_id = start_resp.json()["id"]

        hb = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={
                "is_fullscreen": True,
                "fullscreen_exited_increment": 2,
                "agent_blocked_count": 7,
            },
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(hb.status_code, 200)

        # Regressing counter is ignored (monotonic store)
        hb2 = self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={"is_fullscreen": True, "agent_blocked_count": 3},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(hb2.status_code, 200)

        mon = self.client.post(
            f"/api/exams/{session_id}/force-submit",
            headers=self._auth_header(self.teacher_token),
        )
        self.assertEqual(mon.status_code, 200)
        mon_data = self.client.get(
            f"/api/exams/{self.problem.id}/monitor",
            headers=self._auth_header(self.teacher_token),
        ).json()
        sess = mon_data["sessions"][0]
        self.assertEqual(sess["fullscreen_exited_count"], 2)
        self.assertEqual(sess["agent_blocked_count"], 7)

    def test_teacher_proctor_monitor_and_force_submit(self):
        # Alice starts session and records a violation
        start_resp = self.client.post(
            f"/api/exams/{self.problem.id}/start",
            headers=self._auth_header(self.student_token),
        )
        session_id = start_resp.json()["id"]

        self.client.post(
            f"/api/exams/{session_id}/heartbeat",
            json={"is_fullscreen": True, "blur_increment": 1, "violation_event": "FOCUS_LOST"},
            headers=self._auth_header(self.student_token),
        )

        # Student cannot access proctor monitor
        student_mon = self.client.get(
            f"/api/exams/{self.problem.id}/monitor",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(student_mon.status_code, 403)

        # Teacher accesses proctor monitor
        teacher_mon = self.client.get(
            f"/api/exams/{self.problem.id}/monitor",
            headers=self._auth_header(self.teacher_token),
        )
        self.assertEqual(teacher_mon.status_code, 200)
        mon_data = teacher_mon.json()
        self.assertEqual(mon_data["problem_id"], self.problem.id)
        self.assertEqual(mon_data["active_students"], 1)
        self.assertEqual(len(mon_data["sessions"]), 1)
        self.assertEqual(mon_data["sessions"][0]["student_username"], "alice")
        self.assertEqual(mon_data["sessions"][0]["blur_count"], 1)
        self.assertEqual(len(mon_data["sessions"][0]["violations"]), 1)

        # Teacher force submits the session
        force_resp = self.client.post(
            f"/api/exams/{session_id}/force-submit",
            headers=self._auth_header(self.teacher_token),
        )
        self.assertEqual(force_resp.status_code, 200)

        # Verify session is now submitted
        teacher_mon_after = self.client.get(
            f"/api/exams/{self.problem.id}/monitor",
            headers=self._auth_header(self.teacher_token),
        )
        self.assertEqual(teacher_mon_after.json()["sessions"][0]["status"], "submitted")

    def test_exam_lock_ticket_flow(self):
        """Student requests an exam ticket and uses it to satisfy strict lock."""
        # Non-exam problem rejects ticket
        resp = self.client.post(
            f"/api/exams/{self.plain_problem.id}/ticket",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 400)

        # Exam problem issues valid HMAC ticket
        resp = self.client.post(
            f"/api/exams/{self.strict_problem.id}/ticket",
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("ticket", data)
        self.assertEqual(data["ticket"], exam_agent_token(self.student.id, self.strict_problem.id))

        # Starting strict exam with this ticket succeeds
        start_resp = self.client.post(
            f"/api/exams/{self.strict_problem.id}/start",
            json={"agent_lock_token": data["ticket"]},
            headers=self._auth_header(self.student_token),
        )
        self.assertEqual(start_resp.status_code, 200)
        self.assertTrue(start_resp.json()["agent_lock_verified"])


if __name__ == "__main__":
    unittest.main()
