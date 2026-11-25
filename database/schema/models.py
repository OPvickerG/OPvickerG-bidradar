"""Relational schema (SQLAlchemy 2). Portable: PostgreSQL in production, SQLite for local tests.

All timestamps are timezone-aware UTC. Unique constraints give stable identities so repeated scans
never duplicate tenders, matches, documents or notifications."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.timeutil import utcnow


class Base(DeclarativeBase):
    pass


def ts(nullable: bool = True, default=None):
    return mapped_column(DateTime(timezone=True), nullable=nullable, default=default)


# --------------------------------------------------------------------------- sources & taxonomy
class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # enabled | disabled | requires_auth | requires_captcha | inaccessible | unreliable
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    last_success_at: Mapped[Optional[datetime]] = ts()
    last_error_at: Mapped[Optional[datetime]] = ts()
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = ts(False, utcnow)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    keywords: Mapped[list["Keyword"]] = relationship(back_populates="category", cascade="all, delete-orphan")


class Keyword(Base):
    """kind: phrase (strong, user-facing service names), synonym (alternative wording),
    discovery (short search term sent to portals), negative (context that rules a match out),
    context (supporting terms that only add weight alongside another hit)."""
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("category_id", "phrase", "kind", name="uq_keyword"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    phrase: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="phrase")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_defined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category: Mapped[Category] = relationship(back_populates="keywords")


# --------------------------------------------------------------------------- tenders
class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source_key", "external_id", name="uq_tender_source_external"),
        Index("ix_tender_score", "relevance_score"),
        Index("ix_tender_deadline", "deadline_at"),
        Index("ix_tender_status", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)     # portal's stable id
    bid_number: Mapped[Optional[str]] = mapped_column(String(128))            # e.g. GEM/2026/B/8209707
    ra_number: Mapped[Optional[str]] = mapped_column(String(128))
    parent_bid_number: Mapped[Optional[str]] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    procurement_type: Mapped[Optional[str]] = mapped_column(String(64))      # product / service / works
    organization: Mapped[Optional[str]] = mapped_column(String(300))
    department: Mapped[Optional[str]] = mapped_column(String(300))
    ministry: Mapped[Optional[str]] = mapped_column(String(300))
    location: Mapped[Optional[str]] = mapped_column(String(300))
    state: Mapped[Optional[str]] = mapped_column(String(100))
    published_at: Mapped[Optional[datetime]] = ts()
    source_updated_at: Mapped[Optional[datetime]] = ts()
    deadline_at: Mapped[Optional[datetime]] = ts()
    opening_at: Mapped[Optional[datetime]] = ts()
    # active | closed | in_evaluation | awarded | cancelled | unknown
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    stage: Mapped[Optional[str]] = mapped_column(String(64))                 # portal's own stage label
    quantity: Mapped[Optional[float]] = mapped_column(Float)
    estimated_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    emd: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    performance_security: Mapped[Optional[str]] = mapped_column(String(100))
    contract_duration: Mapped[Optional[str]] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # eligibility, issues, urls, raw facts
    relevance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_category: Mapped[Optional[str]] = mapped_column(String(8))
    match_explanation: Mapped[Optional[str]] = mapped_column(Text)
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    official_url: Mapped[Optional[str]] = mapped_column(String(600))
    document_url: Mapped[Optional[str]] = mapped_column(String(600))
    first_seen_at: Mapped[datetime] = ts(False, utcnow)
    last_seen_at: Mapped[datetime] = ts(False, utcnow)
    last_verified_at: Mapped[Optional[datetime]] = ts()
    last_refreshed_at: Mapped[Optional[datetime]] = ts()

    matches: Mapped[list["TenderMatch"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    documents: Mapped[list["TenderDocument"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    # the long document text lives in its own table so listing/refresh queries never pull it (seen 25 Nov 2026:
    # loading it with every row used up the database's 5 GB/month transfer allowance in six days)
    text: Mapped[Optional["TenderText"]] = relationship(back_populates="tender", uselist=False,
                                                        cascade="all, delete-orphan")


class TenderText(Base):
    __tablename__ = "tender_texts"
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True)
    doc_text: Mapped[Optional[str]] = mapped_column(Text)          # first DOC_TEXT_KEEP characters of the bid document
    doc_consignee: Mapped[Optional[str]] = mapped_column(Text)     # delivery/consignee sections of the WHOLE document
    updated_at: Mapped[Optional[datetime]] = ts(True, utcnow)
    tender: Mapped[Tender] = relationship(back_populates="text")


def _text_row(t) -> Optional[TenderText]:
    try:
        return t.text
    except Exception:              # detached / transient object without a session
        return None


def doc_text_of(t) -> str:
    """Stored bid-document text of a tender ('' if none). Older rows kept it inside details."""
    row = _text_row(t)
    if row is not None and row.doc_text:
        return row.doc_text
    return (getattr(t, "details", None) or {}).get("doc_text") or ""


def doc_consignee_of(t) -> Optional[str]:
    """Consignee sections of the whole document; None if never stored (document read before 25 Nov 2026)."""
    row = _text_row(t)
    if row is not None and row.doc_consignee is not None:
        return row.doc_consignee
    return (getattr(t, "details", None) or {}).get("doc_consignee")


def store_doc_text(t, text: str, consignee: str) -> None:
    text, consignee = (text or "").replace("\x00", ""), (consignee or "").replace("\x00", "")   # Postgres refuses NUL
    row = _text_row(t)
    if row is None:
        t.text = TenderText(doc_text=text, doc_consignee=consignee)
    else:
        row.doc_text, row.doc_consignee, row.updated_at = text, consignee, utcnow()
    d = dict(t.details or {})
    if "doc_text" in d or "doc_consignee" in d:
        d.pop("doc_text", None)
        d.pop("doc_consignee", None)
        t.details = d


class TenderMatch(Base):
    __tablename__ = "tender_matches"
    __table_args__ = (UniqueConstraint("tender_id", "category_code", name="uq_match"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    category_code: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    computed_at: Mapped[datetime] = ts(False, utcnow)
    tender: Mapped[Tender] = relationship(back_populates="matches")


class TenderDocument(Base):
    __tablename__ = "tender_documents"
    __table_args__ = (UniqueConstraint("tender_id", "url", name="uq_tender_document"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)       # bid_document | ra_document | corrigendum | result_page
    url: Mapped[str] = mapped_column(String(600), nullable=False)
    # pending | extracted | no_text_layer | ocr_unavailable | failed | skipped | too_large
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    pages: Mapped[Optional[int]] = mapped_column(Integer)
    text_excerpt: Mapped[Optional[str]] = mapped_column(Text)            # redacted, truncated
    extracted_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    fetched_at: Mapped[Optional[datetime]] = ts()
    tender: Mapped[Tender] = relationship(back_populates="documents")


class TenderEvent(Base):
    """Tender status history: every detected change, timestamped (old value retained)."""
    __tablename__ = "tender_status_history"
    __table_args__ = (Index("ix_event_tender", "tender_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scan_executions.id", ondelete="SET NULL"))
    # discovered | deadline_changed | status_changed | corrigendum_published | eligibility_changed |
    # quantity_changed | value_changed | cancelled | outcome_available | specification_changed
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    field: Mapped[Optional[str]] = mapped_column(String(64))
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    significant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_at: Mapped[datetime] = ts(False, utcnow)


class BidObservation(Base):
    """One row per (tender, scan) observation: what the portal showed at that moment."""
    __tablename__ = "bid_observations"
    __table_args__ = (UniqueConstraint("tender_id", "scan_id", name="uq_observation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scan_executions.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = ts(False, utcnow)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


# --------------------------------------------------------------------------- scanning
class ScanExecution(Base):
    __tablename__ = "scan_executions"
    __table_args__ = (UniqueConstraint("slot_at", "trigger", name="uq_scan_slot_trigger"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_at: Mapped[datetime] = ts(False)                     # the :00/:30 slot this run belongs to
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduler")  # scheduler|manual|recovery|missed
    # running | succeeded | partial | failed | missed | skipped_paused | skipped_locked
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    started_at: Mapped[Optional[datetime]] = ts()
    finished_at: Mapped[Optional[datetime]] = ts()
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128))


class ScanError(Base):
    __tablename__ = "scan_errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scan_executions.id", ondelete="CASCADE"))
    source_key: Mapped[Optional[str]] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(48), nullable=False)    # discovery | details | document | refresh | notify
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    tender_ref: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = ts(False, utcnow)


class SchedulerLock(Base):
    """Lease-based distributed lock (works on Postgres and SQLite; Postgres also uses advisory locks)."""
    __tablename__ = "scheduler_locks"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[Optional[str]] = mapped_column(String(128))
    acquired_at: Mapped[Optional[datetime]] = ts()
    expires_at: Mapped[Optional[datetime]] = ts()


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = ts(False, utcnow)


# --------------------------------------------------------------------------- notifications
class EmailNotification(Base):
    __tablename__ = "email_notifications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_dedupe"), Index("ix_notif_status", "status"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    tender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenders.id", ondelete="SET NULL"))
    # new_tender | deadline_changed | corrigendum | eligibility_changed | cancelled | outcome | daily_summary |
    # weekly_summary | test | verification
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(400), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[Optional[str]] = mapped_column(Text)
    recipient: Mapped[Optional[str]] = mapped_column(String(320))
    # pending | held_no_recipient | held_unverified | held_disabled | sending | accepted | delivered |
    # bounced | complained | failed | suppressed_duplicate | cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    provider: Mapped[Optional[str]] = mapped_column(String(32))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = ts()
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = ts(False, utcnow)
    updated_at: Mapped[datetime] = ts(False, utcnow)
    delivered_at: Mapped[Optional[datetime]] = ts()


class EmailDeliveryAttempt(Base):
    __tablename__ = "email_delivery_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("email_notifications.id", ondelete="CASCADE"), nullable=False)
    attempted_at: Mapped[datetime] = ts(False, utcnow)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)   # accepted | rejected | error | provider_event
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    detail: Mapped[Optional[str]] = mapped_column(Text)


class SuppressedNotification(Base):
    """Audit of notifications NOT created because an identical one already exists."""
    __tablename__ = "suppressed_notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = ts(False, utcnow)


# --------------------------------------------------------------------------- settings
class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = ts(False, utcnow)
