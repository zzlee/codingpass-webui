"""Authentication and authorisation primitives for the CodingPass API.

Three concerns live here:

1. **Password storage** - :func:`hash_password` / :func:`verify_password` wrap
   ``bcrypt`` (adaptive cost factor, per-hash salt, constant-time compare).
2. **Tokens** - :func:`create_access_token` issues stateless JWTs signed with
   HMAC-SHA256; :func:`decode_access_token` validates signature, expiry, issuer
   and shape and never leaks *why* a token was rejected to the caller's message.
3. **Route guards** - FastAPI dependencies used by ``main.py``:
   ``get_db``, ``get_current_user`` and the :func:`require_roles` factory.

Only ``Authorization: Bearer <token>`` is accepted; credentials are never read
from query strings, because URLs end up in access logs and browser history.

Configuration (environment)
---------------------------
``JWT_SECRET_KEY``  HS256 signing key. **Always set this in production.** When
                    unset, a 256-bit key is generated once and persisted to
                    ``backend/.jwt_secret`` (mode ``0600``) so tokens survive a
                    restart during development.
``JWT_TTL_MINUTES`` access-token lifetime in minutes (default ``720`` = 12 h).
``JWT_ISSUER``      ``iss`` claim and the value required on decode
                    (default ``codingpass-api``).
``BCRYPT_ROUNDS``   bcrypt cost factor (default ``12``; keep >= 10).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

try:  # package-style launch: `uvicorn backend.main:app`
    from .database import get_db
    from .models import Role, User
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    from database import get_db  # type: ignore[no-redef]  # noqa: F401
    from models import Role, User  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.auth")

__all__ = [
    "bearer_scheme",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "get_db",
    "get_optional_user",
    "hash_password",
    "require_roles",
    "require_admin",
    "require_student",
    "require_teacher",
    "exam_agent_token",
    "token_subject_id",
    "verify_exam_agent_token",
    "verify_password",
    "verify_password_for_login",
]

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

#: bcrypt refuses passwords longer than 72 bytes; we truncate instead of failing.
MAX_PASSWORD_BYTES = 72
BCRYPT_ROUNDS = max(10, int(os.environ.get("BCRYPT_ROUNDS", "12")))
JWT_ALGORITHM = "HS256"
JWT_ISSUER = os.environ.get("JWT_ISSUER", "codingpass-api").strip() or "codingpass-api"
JWT_AUDIENCE: Optional[str] = (os.environ.get("JWT_AUDIENCE", "").strip() or None)
ACCESS_TOKEN_TTL_SECONDS = max(60, int(os.environ.get("JWT_TTL_MINUTES", "720"))) * 60
#: Where the auto-generated development key is kept.
SECRET_FILE = Path(__file__).resolve().parent / ".jwt_secret"


def _load_secret() -> tuple[str, str]:
    """Resolve the signing key, returning ``(key, human_readable_source)``.

    An operator-supplied key wins. Otherwise one is generated and stored next to
    the database with owner-only permissions, so restarting the server does not
    log everybody out - and we do not fall back to a hard-coded key that an
    attacker could use to mint admin tokens.
    """
    configured = os.environ.get("JWT_SECRET_KEY", "").strip()
    if configured:
        return configured, "JWT_SECRET_KEY"

    try:
        if SECRET_FILE.exists():
            stored = SECRET_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored, f"generated key in {SECRET_FILE.name}"
        secret = secrets.token_urlsafe(48)
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secret)
        return secret, f"generated key in {SECRET_FILE.name} (JWT_SECRET_KEY is unset)"
    except OSError:
        # Read-only filesystem or permission problem: a volatile key is still
        # safer than a constant one; tokens simply die with the process.
        logger.exception(
            "could not persist a signing key to %s; using an in-memory key "
            "that invalidates all tokens on restart",
            SECRET_FILE,
        )
        return secrets.token_urlsafe(48), "ephemeral in-memory key"


SECRET_KEY, SECRET_SOURCE = _load_secret()

if len(SECRET_KEY.encode("utf-8")) < 32:
    logger.warning(
        "the JWT signing key (%s) is shorter than 32 bytes; tokens are weaker "
        "than RFC 7518 recommends for HS256",
        SECRET_SOURCE,
    )
if not os.environ.get("JWT_SECRET_KEY", "").strip():
    logger.warning(
        "JWT_SECRET_KEY is not set - %s. Set a dedicated secret before "
        "exposing this service.",
        SECRET_SOURCE,
    )

#: ``auto_error=False`` lets us emit our own 401 body (plus the challenge header)
#: so the frontend gets a consistent ``{"detail": ...}`` shape.
bearer_scheme = HTTPBearer(auto_error=False, description="Paste the JWT from /api/auth/login")


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #


def _password_bytes(plain: str) -> bytes:
    """UTF-8 encode a password and clamp it to bcrypt's 72-byte input limit.

    The cut is re-decoded with ``errors='ignore'`` so a multibyte character that
    straddles the boundary is dropped whole instead of producing an invalid
    sequence: hashing must never raise on user input.
    """
    encoded = (plain or "").encode("utf-8")
    if len(encoded) <= MAX_PASSWORD_BYTES:
        return encoded
    return encoded[:MAX_PASSWORD_BYTES].decode("utf-8", errors="ignore").encode("utf-8")


def hash_password(plain_password: str) -> str:
    """Return a salted bcrypt digest for ``plain_password``."""
    digest = bcrypt.hashpw(
        _password_bytes(plain_password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    )
    return digest.decode("ascii")


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Constant-time check of ``plain_password`` against a stored digest."""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            _password_bytes(plain_password), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        # Stored digest is malformed / not bcrypt at all: treat as "no match"
        # rather than letting a 500 escape the login endpoint.
        logger.warning("stored password hash is not a valid bcrypt digest")
        return False


_TIMING_LOCK = threading.Lock()
_TIMING_DIGEST: Optional[str] = None


def verify_password_for_login(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Login-time check whose cost does not depend on the account existing.

    Without this, "no such user" answers measurably faster than "wrong
    password", which hands an attacker a free username oracle.
    """
    global _TIMING_DIGEST
    with _TIMING_LOCK:
        if _TIMING_DIGEST is None:
            _TIMING_DIGEST = bcrypt.hashpw(
                b"codingpass-timing-equaliser", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
            ).decode("ascii")
    return verify_password(plain_password, hashed_password or _TIMING_DIGEST)


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


class TokenError(Exception):
    """Raised when a token cannot be trusted. ``reason`` is for the logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExpiredTokenError(TokenError):
    """Distinct so the API can tell the client to log in again."""

    def __init__(self) -> None:
        super().__init__("token expired")


def exam_agent_token(student_id: int, problem_id: int) -> str:
    """HMAC-SHA256 proof that the Local Agent keyboard lockdown is engaged.

    The token is bound to the exact ``(student_id, problem_id)`` pair and
    derived with HMAC-SHA256 (``hashlib.sha256`` digest) over the server
    ``SECRET_KEY``, so it cannot be guessed offline, forged from the browser,
    or replayed for a different student/problem.  The trusted Local Agent
    channel surfaces this value and the client replays it to
    ``POST /api/exams/{problem_id}/start``; verification happens in
    :func:`verify_exam_agent_token`.
    """
    message = f"codingpass:exam-lock:v1:{int(student_id)}:{int(problem_id)}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_exam_agent_token(token: Optional[str], student_id: int, problem_id: int) -> bool:
    """Constant-time check of an agent lock token against the expected HMAC."""
    if not token:
        return False
    expected = exam_agent_token(student_id, problem_id)
    candidate = token.strip()
    if len(candidate) != len(expected):
        return False
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    user: User,
    ttl_seconds: Optional[int] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> tuple[str, int]:
    """Mint a bearer token for ``user``.

    Returns ``(token, expires_in_seconds)`` so the caller can advertise the
    lifetime to the client without recomputing it.

    ``sub`` carries the user id; ``role`` is duplicated into the payload purely
    for client-side menu rendering - authorisation always re-reads the row from
    the database, so a role change takes effect on the next request.
    """
    ttl = int(ttl_seconds) if ttl_seconds else ACCESS_TOKEN_TTL_SECONDS
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    if JWT_AUDIENCE is None:
        payload.pop("aud")
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)
    if isinstance(token, bytes):  # PyJWT < 2 compatibility
        token = token.decode("ascii")
    return token, ttl


def decode_access_token(token: str) -> dict[str, Any]:
    """Validate and decode a JWT.

    Raises:
        ExpiredTokenError: signature is fine but ``exp`` has passed.
        TokenError: anything else - bad signature, wrong algorithm, malformed
            payload, missing subject, wrong issuer/audience.
    """
    if not token or not token.strip():
        raise TokenError("empty token")

    options = {"require": ["exp", "sub"]}
    try:
        payload = jwt.decode(
            token.strip(),
            SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options=options,
            leeway=30,  # tolerate small clock skew between nodes
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError() from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid token: {exc.__class__.__name__}: {exc}") from exc

    subject = payload.get("sub")
    if subject is None or not str(subject).strip().isdigit():
        raise TokenError("token subject is not a user id")
    return payload


def token_subject_id(payload: dict[str, Any]) -> int:
    """Extract the numeric user id from a decoded payload."""
    return int(str(payload["sub"]).strip())


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=dict(_UNAUTHENTICATED_HEADERS),
    )


async def get_current_user(
    db: Session = Depends(get_db),
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)] = None,
) -> User:
    """Resolve the bearer token to a :class:`models.User`, or raise ``401``.

    The user row is always re-read, so a deleted account or a changed role takes
    effect immediately even though the token itself is still cryptographically
    valid.
    """
    if credentials is None or not (credentials.credentials or "").strip():
        raise _unauthorized("Not authenticated: provide 'Authorization: Bearer <token>'.")

    token = credentials.credentials.strip()
    try:
        payload = decode_access_token(token)
    except ExpiredTokenError:
        raise _unauthorized("Session expired; please log in again.") from None
    except TokenError as exc:
        logger.debug("rejected a token: %s", exc.reason)
        raise _unauthorized("Invalid authentication token.") from None

    user_id = token_subject_id(payload)
    user = db.get(User, user_id)
    if user is None:
        # Account removed after the token was issued.
        raise _unauthorized("This account no longer exists.")
    return user


async def get_optional_user(
    db: Session = Depends(get_db),
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)] = None,
) -> Optional[User]:
    """Resolve the caller when a valid token is present, else ``None``.

    Used only by endpoints whose behaviour merely *changes* with a token (for
    example public registration, where an admin may create staff accounts).
    """
    if credentials is None or not (credentials.credentials or "").strip():
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError:
        return None
    return db.get(User, token_subject_id(payload))


def require_roles(*allowed_roles: str) -> Callable[..., Any]:
    """Build a dependency that admits only the given roles.

    Usage::

        Teacher = require_roles(Role.TEACHER, Role.ADMIN)

        @app.post("/api/problems")
        async def create_problem(user: User = Depends(Teacher)): ...

    Role names are validated at import time so a typo fails loudly at startup
    instead of silently locking everybody out.
    """
    if not allowed_roles:
        raise ValueError("require_roles() needs at least one role")
    unknown = [role for role in allowed_roles if role not in Role.ALL]
    if unknown:
        raise ValueError(f"unknown role(s) {unknown}; expected a subset of {list(Role.ALL)}")
    allowed = frozenset(allowed_roles)
    label = " or ".join(sorted(allowed))

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            logger.info(
                "403: user id=%s role=%s attempted a %s-only endpoint",
                user.id,
                user.role,
                label,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {label} role.",
            )
        return user

    _guard.__name__ = f"require_{'_or_'.join(sorted(allowed))}"
    return _guard


#: Ready-made guards used throughout ``main.py``.
require_student = require_roles(Role.STUDENT)
require_teacher = require_roles(Role.TEACHER, Role.ADMIN)
require_admin = require_roles(Role.ADMIN)


if __name__ == "__main__":  # pragma: no cover - developer convenience
    logging.basicConfig(level="INFO", format="%(levelname)-8s %(name)s: %(message)s")

    class _Stub:  # minimal stand-in with the attributes create_access_token reads
        id, username, role = 1, "admin", "admin"

    signed, lifetime = create_access_token(_Stub())  # type: ignore[arg-type]
    print(f"signing key source: {SECRET_SOURCE}")
    print(f"token (valid {lifetime}s): {signed[:32]}...")
    print(f"decoded: {decode_access_token(signed)}")
