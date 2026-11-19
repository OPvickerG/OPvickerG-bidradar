"""Runtime configuration, read only from environment variables (never hard-coded secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- storage -----------------------------------------------------------
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./bidradar.db"))

    # --- app / security ----------------------------------------------------
    app_name: str = "OPvickerG BidRadar"
    environment: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080"))
    session_secret: Optional[str] = field(default_factory=lambda: os.environ.get("SESSION_SECRET"))
    admin_username: str = field(default_factory=lambda: os.environ.get("ADMIN_USERNAME", "admin"))
    admin_password_hash: Optional[str] = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD_HASH"))
    # token that Cloud Scheduler / internal callers present to /internal/* endpoints
    internal_task_token: Optional[str] = field(default_factory=lambda: os.environ.get("INTERNAL_TASK_TOKEN"))
    secure_cookies: bool = field(default_factory=lambda: _bool("SECURE_COOKIES", True))
    # address allowed to sign in with an emailed link (no password needed)
    admin_email: Optional[str] = field(default_factory=lambda: (os.environ.get("ADMIN_EMAIL") or "").strip().lower() or None)
    # run the 30-minute scanner inside the web process (single free service) and keep the service awake
    run_scheduler: bool = field(default_factory=lambda: _bool("RUN_SCHEDULER", False))
    keepalive: bool = field(default_factory=lambda: _bool("KEEPALIVE", False))

    # --- scanner -------------------------------------------------------------
    request_delay_seconds: float = field(default_factory=lambda: _float("SCANNER_REQUEST_DELAY", 3.0))
    request_timeout_seconds: float = field(default_factory=lambda: _float("SCANNER_REQUEST_TIMEOUT", 60.0))
    max_pages_per_term: int = field(default_factory=lambda: _int("SCANNER_MAX_PAGES_PER_TERM", 2))
    backfill_pages_per_term: int = field(default_factory=lambda: _int("SCANNER_BACKFILL_PAGES_PER_TERM", 4))
    max_documents_per_scan: int = field(default_factory=lambda: _int("SCANNER_MAX_DOCUMENTS_PER_SCAN", 40))
    max_refreshes_per_scan: int = field(default_factory=lambda: _int("SCANNER_MAX_REFRESHES_PER_SCAN", 40))
    scan_lease_minutes: int = field(default_factory=lambda: _int("SCAN_LEASE_MINUTES", 25))
    scan_interval_minutes: int = 30
    source_failure_threshold: int = field(default_factory=lambda: _int("SOURCE_FAILURE_THRESHOLD", 3))
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "SCANNER_USER_AGENT",
        "OPvickerG-BidRadar/1.0 (public tender monitoring; polite, throttled)"))

    # --- semantic matching ---------------------------------------------------
    embedding_backend: str = field(default_factory=lambda: os.environ.get("EMBEDDING_BACKEND", "none"))  # none | fastembed

    # --- email -------------------------------------------------------------
    email_provider: str = field(default_factory=lambda: os.environ.get("EMAIL_PROVIDER", "console"))  # console | resend | smtp
    email_from: Optional[str] = field(default_factory=lambda: os.environ.get("EMAIL_FROM"))
    resend_api_key: Optional[str] = field(default_factory=lambda: os.environ.get("RESEND_API_KEY"))
    resend_webhook_secret: Optional[str] = field(default_factory=lambda: os.environ.get("RESEND_WEBHOOK_SECRET"))
    smtp_host: Optional[str] = field(default_factory=lambda: os.environ.get("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_username: Optional[str] = field(default_factory=lambda: os.environ.get("SMTP_USERNAME"))
    smtp_password: Optional[str] = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD"))
    email_max_attempts: int = field(default_factory=lambda: _int("EMAIL_MAX_ATTEMPTS", 5))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Used by tests after changing environment variables."""
    global _settings
    _settings = None
