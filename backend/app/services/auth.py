"""Authentication & role management.

One user table and one login for everybody; the user's role (passenger,
operator, admin) decides which dashboard they are routed to and which API
endpoints they may call. Roles are read from the database on every request,
so a role change by the master admin applies immediately.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from app.models import Identity, Role, UserOut

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = timedelta(hours=12)
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=5)
RESET_CODE_TTL = timedelta(minutes=15)
RESET_CODE_MAX_ATTEMPTS = 5
RESET_REQUEST_INTERVAL = timedelta(seconds=60)

SESSION_COOKIE = "st_session"
HOME_PAGES = {Role.PASSENGER: "/passenger.html", Role.OPERATOR: "/operator.html", Role.ADMIN: "/operator.html"}
ASSIGNABLE_ROLES = {Role.PASSENGER, Role.OPERATOR}  # there is a single master admin


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    """Only a hash of the session token is stored, never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def _code_hash(user_id: int, code: str) -> str:
    return hashlib.sha256(f"{user_id}:{code}".encode()).hexdigest()


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthService:
    def __init__(self, repository, clock, mailer) -> None:
        self._repo = repository
        self._clock = clock
        self._mailer = mailer
        self._failures: dict[str, deque] = defaultdict(deque)

    # ---------- helpers ----------

    def _identity(self, row) -> Identity:
        return Identity(user_id=row["id"], email=row["email"], role=Role(row["role"]))

    def _new_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self._repo.create_session(token_hash(token), user_id, self._clock.now() + SESSION_TTL)
        return token

    def _check_throttle(self, email: str) -> None:
        now, attempts = self._clock.now(), self._failures[email]
        while attempts and now - attempts[0] > FAILED_LOGIN_WINDOW:
            attempts.popleft()
        if len(attempts) >= MAX_FAILED_LOGINS:
            raise AuthError("Too many failed attempts. Try again in a few minutes.", 429)

    # ---------- login / sessions ----------

    def register_passenger(self, email: str, password: str) -> tuple[Identity, str]:
        user_id = self._repo.create_user(email, hash_password(password), Role.PASSENGER, self._clock.now())
        if user_id is None:
            raise AuthError("An account with this email already exists", 409)
        return Identity(user_id=user_id, email=email, role=Role.PASSENGER), self._new_session(user_id)

    def login(self, email: str, password: str) -> tuple[Identity, str]:
        self._check_throttle(email)
        row = self._repo.user_by_email(email)
        if row is None or not verify_password(password, row["password_hash"]):
            self._failures[email].append(self._clock.now())
            raise AuthError("Invalid email or password")
        self._failures.pop(email, None)
        return self._identity(row), self._new_session(row["id"])

    def resolve(self, token: Optional[str]) -> Optional[Identity]:
        if not token:
            return None
        row = self._repo.session_user(token_hash(token), self._clock.now())
        return self._identity(row) if row else None

    def logout(self, token: Optional[str]) -> None:
        if token:
            self._repo.delete_session(token_hash(token))

    def ensure_admin(self, email: str, password: str) -> None:
        self._repo.upsert_admin(email, hash_password(password), self._clock.now())

    # ---------- forgot password ----------

    async def request_password_reset(self, email: str) -> None:
        """E-mail a 6-digit code. Silent for unknown e-mails (no account enumeration)."""
        row = self._repo.user_by_email(email)
        if row is None:
            return
        now = self._clock.now()
        existing = self._repo.reset_code(row["id"])
        if existing and now - datetime.fromisoformat(existing["created_at"]) < RESET_REQUEST_INTERVAL:
            return  # one code per minute is enough; prevents mail flooding
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._repo.save_reset_code(row["id"], _code_hash(row["id"], code), now + RESET_CODE_TTL, now)
        minutes = int(RESET_CODE_TTL.total_seconds() // 60)
        await self._mailer.send(
            email, "Baku Smart Transit - password reset code",
            f"Your verification code is {code}.\nIt expires in {minutes} minutes. "
            "If you did not request a password reset, ignore this e-mail.",
        )

    def reset_password(self, email: str, code: str, new_password: str) -> None:
        invalid = AuthError("Invalid or expired verification code", 400)
        row = self._repo.user_by_email(email)
        if row is None:
            raise invalid
        reset = self._repo.reset_code(row["id"])
        if (reset is None or datetime.fromisoformat(reset["expires_at"]) < self._clock.now()
                or reset["attempts"] >= RESET_CODE_MAX_ATTEMPTS):
            raise invalid
        if not hmac.compare_digest(reset["code_hash"], _code_hash(row["id"], code)):
            self._repo.count_reset_attempt(row["id"])
            raise invalid
        self._repo.set_password(row["id"], hash_password(new_password))
        self._repo.delete_reset_code(row["id"])
        self._repo.delete_user_sessions(row["id"])  # sign out everywhere after a reset
        self._failures.pop(email, None)

    # ---------- role management (master admin) ----------

    def set_role(self, admin: Identity, user_id: int, role: Role) -> UserOut:
        row = self._repo.user_by_id(user_id)
        if row is None:
            raise AuthError(f"User {user_id} not found", 404)
        if row["id"] == admin.user_id or Role(row["role"]) is Role.ADMIN:
            raise AuthError("The master admin's role cannot be changed", 409)
        if role not in ASSIGNABLE_ROLES:
            raise AuthError("Only the passenger and operator roles can be assigned", 422)
        self._repo.set_role(user_id, role)
        self._repo.delete_user_sessions(user_id)  # next login routes them to their new dashboard
        return self._repo.to_user(self._repo.user_by_id(user_id))
