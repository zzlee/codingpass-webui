"""SQLAlchemy engine, session plumbing and bootstrap seed data.

The whole persistence layer lives behind three names:

``engine`` / ``SessionLocal``
    One process-wide SQLAlchemy engine bound to the SQLite file
    ``backend/codingpass.db`` (override with ``DATABASE_URL``).
``get_db()``
    FastAPI dependency that yields a session and always closes it.
``init_db()``
    Creates every table and inserts the demo dataset when it is missing.
    Called from the FastAPI lifespan handler in ``main.py``.

Timestamps are stored as **naive UTC** (``utcnow()``): SQLite ``DATETIME`` has no
timezone column, so keeping one convention everywhere avoids the classic
"server timezone vs browser timezone" drift. API responses re-attach UTC before
serialising, so clients always receive an explicit offset.

Configuration (environment)
---------------------------
``DATABASE_URL``   any SQLAlchemy URL; defaults to ``sqlite:///<backend>/codingpass.db``
``DATABASE_ECHO``  ``1`` to log every statement (debugging only)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("codingpass.db")

#: Directory that holds ``codingpass.db`` and the generated JWT secret.
BASE_DIR = Path(__file__).resolve().parent
#: Default SQLite location (kept inside the project so the app is self-contained).
SQLITE_PATH = BASE_DIR / "codingpass.db"
#: Resolved database URL for this process.
DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip() or f"sqlite:///{SQLITE_PATH}"


def utcnow() -> datetime:
    """Return the current time in UTC, without a ``tzinfo`` (SQLite friendly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in :mod:`models`."""


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _build_engine(url: str) -> Engine:
    """Create the engine, tuned for a file-backed SQLite database under FastAPI.

    * ``check_same_thread=False`` — FastAPI hands sessions to threadpool workers,
      and each request gets its own session from the pool, so the guard is
      unnecessary (and would break ``run_in_threadpool`` usage).
    * ``pool_pre_ping`` — recycles connections that the pool kept open too long.
    * WAL + ``busy_timeout`` — lets a reader never block on a writer and turns
      "database is locked" into a short wait instead of an error.
    * ``foreign_keys=ON`` — SQLite keeps them off by default, per connection.
    """
    connect_args: dict = {}
    if _is_sqlite(url):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        echo=bool(os.environ.get("DATABASE_ECHO", "").strip()),
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if _is_sqlite(url):

        @event.listens_for(engine, "connect")
        def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # pragma: no cover
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


engine = _build_engine(DATABASE_URL)

#: Session factory. ``expire_on_commit=False`` keeps loaded rows readable after
#: the commit that flushed them, which is what the route handlers need when they
#: serialise an object straight after creating it.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Iterator[Session]:
    """Yield a database session for the lifetime of one request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create missing tables, then seed the demo dataset.

    Safe to call on every boot: table creation is idempotent and seeding only
    fills in what is absent. Import failures inside seeding never prevent the
    judge endpoints from starting; they are logged instead.
    """
    try:  # package-style launch: `uvicorn backend.main:app`
        from . import models  # noqa: F401  (registers mappers on Base)
    except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
        import models  # type: ignore[no-redef]  # noqa: F401

    # Create the tables from the metadata the *models* registered themselves on,
    # not from the module-level ``Base``. When this file is executed directly
    # (``python database.py``) it is imported as ``__main__`` **and** as
    # ``database``, giving each copy its own ``Base``; the copy ``models``
    # imported is the only one whose metadata actually knows about the tables.
    metadata = getattr(getattr(models, "Base", None), "metadata", None) or Base.metadata
    metadata.create_all(bind=engine)
    _ensure_submission_columns(engine)
    _ensure_problem_columns(engine)
    _ensure_exam_columns(engine)
    logger.info("database ready at %s", _safe_url(DATABASE_URL))
    try:
        seed_default_data()
    except Exception:  # defensive: a broken seed must not take the judge down
        logger.exception("seed step failed - continuing with the existing data")


#: Phase 4 ``submissions`` columns added after the first release, as
#: ``(column name, SQL type)``. ``create_all`` never alters an existing table, so
#: an older ``codingpass.db`` is upgraded in place instead of being recreated.
#: The Stage 4 rubric columns are ``NOT NULL`` in the ORM, so each of them
#: carries a SQL ``DEFAULT`` here too: SQLite refuses to add a
#: ``NOT NULL``-style column to a populated table without one, and the default
#: doubles as the backfilled value for the historic rows.
SUBMISSION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("ai_feedback", "TEXT"),
    ("ai_suggested_score", "FLOAT"),
    ("ai_model", "VARCHAR(64)"),
    ("ai_reviewed_at", "DATETIME"),
    ("teacher_feedback", "TEXT"),
    ("teacher_score", "FLOAT"),
    ("reviewed_by_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("reviewed_at", "DATETIME"),
    # --- Stage 4: 100-point rubric breakdown ------------------------------- #
    ("compile_score", "FLOAT DEFAULT 0.0"),
    ("sample_score", "FLOAT DEFAULT 0.0"),
    ("random_score", "FLOAT DEFAULT 0.0"),
    ("logic_score", "FLOAT DEFAULT 0.0"),
    ("quality_score", "FLOAT DEFAULT 0.0"),
    ("presentation_warning", "BOOLEAN DEFAULT 0"),
    ("wording_pass", "BOOLEAN DEFAULT 1"),
    ("wording_violations", "TEXT"),
)


#: Problem-pack columns added after the first release, as ``(column name,
#: SQL type)``. ``problem_code`` also gets its ORM index backfilled below.
PROBLEM_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("problem_code", "VARCHAR(32)"),
    ("ref_code", "TEXT"),
    ("ref_language", "VARCHAR(8)"),
    ("teda_random_script", "TEXT"),
    ("wording_rules", "TEXT"),
    ("pdf_path", "VARCHAR(255)"),
    ("is_exam", "BOOLEAN DEFAULT 0"),
    ("exam_lock_required", "BOOLEAN DEFAULT 0"),
    ("max_blur_count", "INTEGER DEFAULT 3"),
    ("tags", "TEXT DEFAULT ''"),
)


#: Exam-telemetry columns added after the first release. ``NOT NULL`` columns
#: carry a SQL ``DEFAULT`` so SQLite can backfill existing rows in place.
EXAM_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("agent_blocked_count", "INTEGER DEFAULT 0"),
)


def _ensure_columns(
    engine: Engine, table: str, migrations: tuple[tuple[str, str], ...]
) -> None:
    """Add missing columns to an existing SQLite ``table`` via in-place ALTERs.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op for a table that already exists, so
    a database created before a column was introduced would otherwise fail every
    read of the new ORM columns. ``PRAGMA table_info`` reports the current
    columns and each absent one is added with ``ALTER TABLE ... ADD COLUMN``;
    nullable columns with no default are a cheap metadata-only operation in
    SQLite. Non-SQLite URLs are left alone (there ``create_all`` plus a real
    migration tool owns the schema).
    """
    if not _is_sqlite(str(engine.url)):
        return

    with engine.begin() as connection:
        existing = {
            str(row[1]).lower()
            for row in connection.execute(
                text(f"PRAGMA table_info({table})")
            ).fetchall()
        }
        if not existing:
            return  # table absent: ``create_all`` just built it with every column.
        for column, sql_type in migrations:
            if column.lower() in existing:
                continue
            connection.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            )
            logger.info("migration: added %s.%s (%s)", table, column, sql_type)


def _ensure_submission_columns(engine: Engine) -> None:
    """Add missing Phase 4 columns to an existing SQLite ``submissions`` table."""
    _ensure_columns(engine, "submissions", SUBMISSION_MIGRATIONS)


def _ensure_exam_columns(engine: Engine) -> None:
    """Add missing anti-cheat telemetry columns to ``exam_sessions``."""
    _ensure_columns(engine, "exam_sessions", EXAM_MIGRATIONS)


def _ensure_problem_columns(engine: Engine) -> None:
    """Add missing problem-pack columns to ``problems`` and backfill the index.

    ``ALTER TABLE ... ADD COLUMN`` cannot carry an ``INDEX`` clause and
    ``create_all`` never touches a pre-existing table, so the ORM index on
    ``problem_code`` (``ix_problems_problem_code``) is created here with
    ``CREATE INDEX IF NOT EXISTS`` once the column itself exists.
    """
    _ensure_columns(engine, "problems", PROBLEM_MIGRATIONS)
    if not _is_sqlite(str(engine.url)):
        return

    with engine.begin() as connection:
        existing = {
            str(row[1]).lower()
            for row in connection.execute(
                text("PRAGMA table_info(problems)")
            ).fetchall()
        }
        if "problem_code" not in existing:
            return  # table absent entirely; create_all already indexed the column.
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_problems_problem_code "
                "ON problems (problem_code)"
            )
        )


def _safe_url(url: str) -> str:
    """Strip any credentials from a URL before it reaches the logs."""
    if "://" in url and "@" in url.split("://", 1)[1]:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.rsplit('@', 1)[-1]}"
    return url


# --------------------------------------------------------------------------- #
# Seed data
# --------------------------------------------------------------------------- #

#: ``(username, email, password, role, full_name)`` for the demo accounts.
#: Plaintext passwords exist here on purpose: this is a classroom demo dataset,
#: and the accounts must be usable out of the box. Change them before production.
DEFAULT_USERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("admin", "admin@codingpass.local", "admin123", "admin", "Site Administrator"),
    ("teacher1", "teacher1@codingpass.local", "teacher123", "teacher", "Prof. Chen"),
    ("student1", "student1@codingpass.local", "student123", "student", "Alice"),
    ("student2", "student2@codingpass.local", "student123", "student", "Bob"),
)

#: Demo classroom: ``(code, name, description, teacher_username, joined_usernames)``.
DEFAULT_CLASSROOMS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "CS101",
        "C/C++ Programming 101",
        "Foundational C / C++ course. Weekly judge exercises with hidden test cases.",
        "teacher1",
        ("student1",),
    ),
)

TWO_SUM_DESCRIPTION = """\
Read two integers `a` and `b` from **standard input** and print their sum to
**standard output**.

### Input
A single line containing two integers `a` and `b`
(-2 000 000 000 <= a, b <= 2 000 000 000), separated by whitespace.

### Output
One line with the value of `a + b`.

### Notes
* Print **only** the number - no labels such as `sum =`, no extra spaces.
* A trailing newline after the answer is recommended (and always accepted).
* Comparison ignores trailing spaces and blank lines, so `5` and `5\\n` both pass.

Every submission is judged against the public sample cases **and** a set of
hidden cases (negative numbers, zero, large values).
"""

PRIME_DESCRIPTION = """\
Read one integer `n` from **standard input** and decide whether it is a prime
number.

### Input
A single line containing one integer `n` (-2 000 000 000 <= n <= 2 000 000 000).

### Output
Print exactly `Prime` or exactly `Not Prime` (capital `P`, one space, no
trailing punctuation).

### Definition
A prime is an integer greater than `1` whose only positive divisors are `1` and
itself. Therefore `0`, `1`, and every negative number are `Not Prime`.

### Hints
* `2` is the smallest - and the only even - prime.
* Trial division up to `sqrt(n)` is enough: test `2`, then the odd candidates
  only, and stop as soon as `d * d > n`. Scanning up to `n` will exceed the time
  limit on the large hidden cases.
* Beware of overflow: compare with `d * d <= n` using a 64-bit integer
  (`long long`), or use `d <= n / d`.
"""

TWO_SUM_STARTER_C = """\
#include <stdio.h>

int main(void) {
    long long a = 0, b = 0;

    /* Read the two operands. Give up politely if the input is malformed. */
    if (scanf("%lld %lld", &a, &b) != 2) {
        return 1;
    }

    /* TODO: replace the 0 below with the sum of a and b. */
    printf("%lld\\n", 0LL);
    return 0;
}
"""

TWO_SUM_STARTER_CPP = """\
#include <iostream>

int main() {
    long long a = 0, b = 0;

    if (!(std::cin >> a >> b)) {
        return 1;
    }

    // TODO: replace the 0 below with the sum of a and b.
    std::cout << 0 << std::endl;
    return 0;
}
"""

PRIME_STARTER_C = """\
#include <stdbool.h>
#include <stdio.h>

static bool is_prime(long long n) {
    /* TODO: return false for n < 2, reject even numbers, then trial-divide
       by the odd candidates only while d * d <= n. */
    return false;
}

int main(void) {
    long long n = 0;

    if (scanf("%lld", &n) != 1) {
        return 1;
    }

    printf("%s\\n", is_prime(n) ? "Prime" : "Not Prime");
    return 0;
}
"""

PRIME_STARTER_CPP = """\
#include <iostream>

static bool is_prime(long long n) {
    // TODO: return false for n < 2, reject even numbers, then trial-divide
    // by the odd candidates only while d * d <= n.
    return false;
}

int main() {
    long long n = 0;

    if (!(std::cin >> n)) {
        return 1;
    }

    std::cout << (is_prime(n) ? "Prime" : "Not Prime") << std::endl;
    return 0;
}
"""

#: Demo problem bank:
#: ``(title, difficulty, description, starter_c, starter_cpp, test_cases)`` where
#: each test case is ``(stdin, expected_stdout, is_sample)``.
DEFAULT_PROBLEMS: tuple[tuple[str, str, str, str, str, tuple[tuple[str, str, bool], ...]], ...] = (
    (
        "Two Sum (A + B Problem)",
        "easy",
        TWO_SUM_DESCRIPTION,
        TWO_SUM_STARTER_C,
        TWO_SUM_STARTER_CPP,
        (
            # Public samples - shown to students with their expected output.
            ("2 3\n", "5\n", True),
            ("10 -4\n", "6\n", True),
            # Hidden - graded, but never disclosed.
            ("-10 25\n", "15\n", False),
            ("100000 200000\n", "300000\n", False),
            ("0 0\n", "0\n", False),
            ("-2147483648 2147483647\n", "-1\n", False),
            ("123456789 987654321\n", "1111111110\n", False),
        ),
    ),
    (
        "Check Prime Number",
        "medium",
        PRIME_DESCRIPTION,
        PRIME_STARTER_C,
        PRIME_STARTER_CPP,
        (
            ("7\n", "Prime\n", True),
            ("10\n", "Not Prime\n", True),
            ("1\n", "Not Prime\n", False),
            ("2\n", "Prime\n", False),
            ("0\n", "Not Prime\n", False),
            ("-7\n", "Not Prime\n", False),
            ("9\n", "Not Prime\n", False),
            ("97\n", "Prime\n", False),
            ("7919\n", "Prime\n", False),
            ("1000000000\n", "Not Prime\n", False),
            ("2147483647\n", "Prime\n", False),
        ),
    ),
)


def seed_default_data(session: Optional[Session] = None) -> bool:
    """Insert any demo user / classroom / problem that is still missing.

    The function is idempotent and granular: an existing deployment that already
    has users keeps them, and only the absent pieces (classrooms, problems) get
    created. Returns ``True`` when at least one row was inserted.
    """
    try:  # package-style launch
        from . import models
        from .auth import hash_password
    except ImportError:  # flat launch
        import models  # type: ignore[no-redef]
        from auth import hash_password  # type: ignore[no-redef]

    User = models.User
    Classroom = models.Classroom
    ClassroomMembership = models.ClassroomMembership
    Problem = models.Problem
    TestCase = models.TestCase

    owns_session = session is None
    db = session or SessionLocal()
    created = False
    try:
        users: dict[str, models.User] = {}
        for username, email, password, role, full_name in DEFAULT_USERS:
            user = db.query(User).filter(User.username == username).one_or_none()
            if user is None:
                user = User(
                    username=username,
                    email=email,
                    hashed_password=hash_password(password),
                    role=role,
                    full_name=full_name,
                )
                db.add(user)
                db.flush()
                created = True
                logger.info("seed: created %s account '%s'", role, username)
            users[username] = user

        for code, name, description, teacher_username, member_names in DEFAULT_CLASSROOMS:
            classroom = db.query(Classroom).filter(Classroom.code == code).one_or_none()
            if classroom is None:
                classroom = Classroom(
                    code=code,
                    name=name,
                    description=description,
                    teacher_id=users[teacher_username].id,
                )
                db.add(classroom)
                db.flush()
                created = True
                logger.info("seed: created classroom '%s' (%s)", name, code)
            for member in member_names:
                student = users[member]
                exists = (
                    db.query(ClassroomMembership)
                    .filter(
                        ClassroomMembership.classroom_id == classroom.id,
                        ClassroomMembership.student_id == student.id,
                    )
                    .one_or_none()
                )
                if exists is None:
                    db.add(
                        ClassroomMembership(
                            classroom_id=classroom.id, student_id=student.id
                        )
                    )
                    db.flush()
                    created = True
                    logger.info("seed: enrolled %s in %s", member, code)

        teacher = users["teacher1"]
        demo_classroom = db.query(Classroom).filter(Classroom.code == "CS101").one_or_none()
        demo_classroom_id = demo_classroom.id if demo_classroom else None
        for (
            title,
            difficulty,
            description,
            starter_c,
            starter_cpp,
            test_cases,
        ) in DEFAULT_PROBLEMS:
            problem = db.query(Problem).filter(Problem.title == title).one_or_none()
            if problem is None:
                problem = Problem(
                    title=title,
                    description=description,
                    difficulty=difficulty,
                    teacher_id=teacher.id,
                    classroom_id=demo_classroom_id,
                    starter_code_c=starter_c,
                    starter_code_cpp=starter_cpp,
                )
                db.add(problem)
                db.flush()
                created = True
                logger.info("seed: created problem '%s'", title)
            if not problem.test_cases:
                for stdin_text, expected, is_sample in test_cases:
                    db.add(
                        TestCase(
                            problem_id=problem.id,
                            input=stdin_text,
                            expected_output=expected,
                            is_sample=is_sample,
                        )
                    )
                db.flush()
                created = True

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()
    return created


if __name__ == "__main__":  # pragma: no cover - developer convenience
    logging.basicConfig(level="INFO", format="%(levelname)-8s %(name)s: %(message)s")
    init_db()
    print(f"Initialised {DATABASE_URL}")
