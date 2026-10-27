"""SQLAlchemy ORM models for CodingPass.

Entity map
----------
``User``        every account, discriminated by ``role`` (admin / teacher / student).
``Classroom``   a course owned by one teacher, joinable through a short ``code``.
``ClassroomMembership``  many-to-many join between a classroom and its students.
``Problem``     a judge exercise with per-problem limits and C / C++ starter code.
                ``classroom_id`` is optional: a problem without one is a private
                draft in the teacher's own bank.
``TestCase``    one ``(stdin, expected stdout)`` pair; ``is_sample`` marks the
                cases that are publicly visible to students.
``Submission``  one judged attempt of a problem by a student, with the aggregate
                verdict (``status``, ``score``, ``passed_count`` / ``total_count``).

Conventions
-----------
* Primary keys are SQLite integers; foreign keys use ``ondelete`` so removing a
  parent never leaves orphans (requires the ``PRAGMA foreign_keys=ON`` that
  ``database.py`` sets on every connection).
* All timestamps are naive UTC produced by :func:`database.utcnow`.
* Enum-like columns are plain ``String`` plus a ``CHECK`` constraint: SQLite has
  no native enum, and raw strings keep migrations and ad-hoc SQL painless. The
  allowed vocabularies live in the :class:`Role`, :class:`Difficulty` and
  :class:`SubmissionStatus` namespaces below and are mirrored into the FastAPI
  request schemas, so bad values are rejected with ``422`` before they reach SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:  # package-style launch: `uvicorn backend.main:app`
    from .database import Base, utcnow
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from database import Base, utcnow  # type: ignore[no-redef]


def _quoted(values: tuple[str, ...]) -> str:
    """Render ``("a", "b")`` as the SQL literal list ``'a', 'b'``."""
    return ", ".join(f"'{value}'" for value in values)


# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #


class Role:
    """Allowed values of :attr:`User.role`."""

    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"
    ALL: tuple[str, ...] = (ADMIN, TEACHER, STUDENT)


class Difficulty:
    """Allowed values of :attr:`Problem.difficulty`."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ALL: tuple[str, ...] = (EASY, MEDIUM, HARD)


class SubmissionStatus:
    """Aggregate verdicts stored on :class:`Submission`.

    ``compiler.Status`` uses ``success`` for a single clean execution; the
    aggregate over a whole test set is reported as ``accepted`` instead.
    """

    ACCEPTED = "accepted"
    WRONG_ANSWER = "wrong_answer"
    TIME_LIMIT_EXCEEDED = "time_limit_exceeded"
    COMPILE_ERROR = "compile_error"
    RUNTIME_ERROR = "runtime_error"
    ALL: tuple[str, ...] = (
        ACCEPTED,
        WRONG_ANSWER,
        TIME_LIMIT_EXCEEDED,
        COMPILE_ERROR,
        RUNTIME_ERROR,
    )


class Language:
    """Languages accepted by the judge (mirrors ``compiler.LANGUAGES``)."""

    C = "c"
    CPP = "cpp"
    ALL: tuple[str, ...] = (C, CPP)


RoleLiteral = Literal["admin", "teacher", "student"]
DifficultyLiteral = Literal["easy", "medium", "hard"]
SubmissionStatusLiteral = Literal[
    "accepted", "wrong_answer", "time_limit_exceeded", "compile_error", "runtime_error"
]


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


class User(Base):
    """A single account, whatever its role."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    #: bcrypt digest produced by :func:`auth.hash_password` (never the password).
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Role.STUDENT, index=True
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        CheckConstraint(f"role IN ({_quoted(Role.ALL)})", name="ck_users_role"),
    )

    # Reverse relations.
    classrooms_teaching: Mapped[list["Classroom"]] = relationship(back_populates="teacher")
    problems: Mapped[list["Problem"]] = relationship(back_populates="teacher")
    memberships: Mapped[list["ClassroomMembership"]] = relationship(
        back_populates="student", cascade="all, delete-orphan", passive_deletes=True
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="student", cascade="all, delete-orphan", passive_deletes=True
    )

    # -- role helpers ------------------------------------------------------ #

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_teacher(self) -> bool:
        return self.role == Role.TEACHER

    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def display_name(self) -> str:
        """Best available human name for this account."""
        return self.full_name or self.username

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


# --------------------------------------------------------------------------- #
# Classrooms
# --------------------------------------------------------------------------- #


class Classroom(Base):
    """A course a teacher owns; students join it with :attr:`code`."""

    __tablename__ = "classrooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Short human-typeable invite code, e.g. ``CS101``. Unique across the site.
    code: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    teacher: Mapped["User"] = relationship(back_populates="classrooms_teaching")
    memberships: Mapped[list["ClassroomMembership"]] = relationship(
        back_populates="classroom", cascade="all, delete-orphan", passive_deletes=True
    )
    problems: Mapped[list["Problem"]] = relationship(back_populates="classroom")

    __table_args__ = (
        CheckConstraint("length(code) >= 3", name="ck_classrooms_code_length"),
        Index("ix_classrooms_teacher_created", "teacher_id", "created_at"),
    )

    # -- membership helpers ------------------------------------------------ #

    @property
    def students(self) -> list["User"]:
        """Enrolled students, oldest membership first."""
        return [membership.student for membership in self._ordered_memberships()]

    def _ordered_memberships(self) -> list["ClassroomMembership"]:
        return sorted(self.memberships, key=lambda m: (m.joined_at, m.id))

    def enrolment_of(self, student_id: int) -> Optional["ClassroomMembership"]:
        """The membership row for ``student_id``, or ``None`` when not enrolled."""
        for membership in self.memberships:
            if membership.student_id == student_id:
                return membership
        return None

    def has_student(self, student_id: int) -> bool:
        return self.enrolment_of(student_id) is not None

    def has_teacher(self, user_id: int) -> bool:
        return self.teacher_id == user_id

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Classroom id={self.id} code={self.code!r} name={self.name!r}>"


class ClassroomMembership(Base):
    """Enrolment of one student in one classroom (unique per pair)."""

    __tablename__ = "classroom_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    classroom: Mapped["Classroom"] = relationship(back_populates="memberships")
    student: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("classroom_id", "student_id", name="uq_classroom_student"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ClassroomMembership classroom_id={self.classroom_id} "
            f"student_id={self.student_id}>"
        )


# --------------------------------------------------------------------------- #
# Problem bank
# --------------------------------------------------------------------------- #


class Problem(Base):
    """A judge exercise, optionally pinned to one classroom."""

    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(
        String(10), nullable=False, default=Difficulty.EASY, index=True
    )
    #: Wall-clock limit per test case handed to ``compiler.run_code``.
    time_limit_sec: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    #: Recorded memory budget for the exercise. The sandbox enforces the global
    #: ``CODE_MEMORY_LIMIT_MB`` ceiling; this value is what the problem advertises
    #: to students and what a future per-run cgroup will apply verbatim.
    memory_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=256)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: ``None`` keeps the problem in the teacher's private bank.
    classroom_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("classrooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    starter_code_c: Mapped[str] = mapped_column(Text, nullable=False, default="")
    starter_code_cpp: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    teacher: Mapped["User"] = relationship(back_populates="problems")
    classroom: Mapped[Optional["Classroom"]] = relationship(back_populates="problems")
    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TestCase.id",
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(f"difficulty IN ({_quoted(Difficulty.ALL)})", name="ck_problems_difficulty"),
        CheckConstraint("time_limit_sec > 0", name="ck_problems_time_limit_positive"),
        CheckConstraint("memory_limit_mb > 0", name="ck_problems_memory_positive"),
        Index("ix_problems_classroom_created", "classroom_id", "created_at"),
    )

    # -- test case helpers -------------------------------------------------- #

    @property
    def sample_test_cases(self) -> list["TestCase"]:
        """Cases students can see (input *and* expected output)."""
        return [case for case in self.test_cases if case.is_sample]

    @property
    def hidden_test_cases(self) -> list["TestCase"]:
        """Graded cases whose input and expected output stay secret."""
        return [case for case in self.test_cases if not case.is_sample]

    def starter_for(self, language: str) -> str:
        """Starter source for ``language`` (``'c'`` or ``'cpp'``)."""
        return self.starter_code_cpp if language == Language.CPP else self.starter_code_c

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Problem id={self.id} title={self.title!r} difficulty={self.difficulty}>"


class TestCase(Base):
    """One graded input/output pair belonging to a :class:`Problem`."""

    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Text piped to the program's stdin.
    input: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Text the program must print on stdout (whitespace-tolerant comparison).
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Public example shown in the problem statement.
    is_sample: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)

    problem: Mapped["Problem"] = relationship(back_populates="test_cases")

    __table_args__ = (Index("ix_test_cases_problem_sample", "problem_id", "is_sample"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TestCase id={self.id} problem_id={self.problem_id} "
            f"is_sample={self.is_sample}>"
        )


# --------------------------------------------------------------------------- #
# Submissions
# --------------------------------------------------------------------------- #


class Submission(Base):
    """The aggregate result of running one submission against every test case."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Full source text, so teachers can review the exact attempt later.
    code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SubmissionStatus.WRONG_ANSWER, index=True
    )
    #: Percentage of passed test cases, 0.0 - 100.0.
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Slowest single test case, in milliseconds.
    execution_time_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    problem: Mapped["Problem"] = relationship(back_populates="submissions")
    student: Mapped["User"] = relationship(back_populates="submissions")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_quoted(SubmissionStatus.ALL)})", name="ck_submissions_status"
        ),
        CheckConstraint(
            f"language IN ({_quoted(Language.ALL)})", name="ck_submissions_language"
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_submissions_score_range"),
        Index("ix_submissions_problem_student", "problem_id", "student_id"),
        Index("ix_submissions_student_created", "student_id", "created_at"),
    )

    @property
    def accepted(self) -> bool:
        return self.status == SubmissionStatus.ACCEPTED

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Submission id={self.id} problem_id={self.problem_id} "
            f"student_id={self.student_id} status={self.status} score={self.score}>"
        )


ALL_MODELS: tuple[type, ...] = (
    User,
    Classroom,
    ClassroomMembership,
    Problem,
    TestCase,
    Submission,
)
