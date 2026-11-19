"""Single-administrator authentication.

* Password stored only as a salted PBKDF2-SHA256 hash (600,000 iterations) in the ADMIN_PASSWORD_HASH secret (never in code or the database).
  Generate one with:  python3 -m core.passwords
* Signed, HttpOnly, SameSite=Lax session cookie (Starlette SessionMiddleware / itsdangerous).
* Per-IP login rate limiting and a CSRF token on every state-changing form."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request

from core.config import get_settings

from core.passwords import hash_password, verify_password  # noqa: E402,F401


class LoginLimiter:
    """At most `limit` failed attempts per IP per `window` seconds (in-memory per instance; Cloud Run runs
    max 1–2 instances for this app, so the effective limit stays small)."""

    def __init__(self, limit: int = 5, window: int = 900):
        self.limit, self.window = limit, window
        self.fails: Dict[str, Deque[float]] = defaultdict(deque)

    def blocked(self, ip: str) -> bool:
        q = self.fails[ip]
        now = time.time()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, ip: str) -> None:
        self.fails[ip].append(time.time())

    def reset(self, ip: str) -> None:
        self.fails.pop(ip, None)


limiter = LoginLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, submitted: Optional[str]) -> None:
    tok = request.session.get("csrf")
    if not tok or not submitted or not hmac.compare_digest(tok, submitted):
        raise HTTPException(status_code=403, detail="Form expired or invalid CSRF token. Reload the page and try again.")


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user")) and request.session.get("pwv") == _pw_version()


def _pw_version() -> str:
    """Changes when the password hash changes, invalidating existing sessions."""
    h = get_settings().admin_password_hash or ""
    return hashlib.sha256(h.encode()).hexdigest()[:12]


def login(request: Request, username: str, password: str) -> bool:
    s = get_settings()
    ok = hmac.compare_digest(username.encode(), s.admin_username.encode()) & verify_password(password, s.admin_password_hash)
    if ok:
        request.session.clear()
        request.session.update({"user": s.admin_username, "pwv": _pw_version(), "at": int(time.time())})
        csrf_token(request)
    return bool(ok)


def auth_configured() -> bool:
    return bool(get_settings().admin_password_hash)


if __name__ == "__main__":
    from core.passwords import main
    main()
