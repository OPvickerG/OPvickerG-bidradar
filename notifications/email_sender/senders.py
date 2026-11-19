"""Email provider adapters. A provider 'accepting' a message is recorded as status=accepted, NOT delivered;
'delivered' only comes from provider delivery events (Resend webhooks)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

import httpx

from core.config import get_settings


@dataclass
class SendResult:
    ok: bool
    provider: str
    message_id: Optional[str] = None
    http_status: Optional[int] = None
    detail: Optional[str] = None
    retryable: bool = True


class EmailSender:
    name = "base"

    def send(self, to: str, subject: str, text: str, html: Optional[str], idempotency_key: str) -> SendResult:
        raise NotImplementedError


class ConsoleSender(EmailSender):
    """Dry-run: nothing leaves the system. Used until a real provider is configured."""
    name = "console"
    outbox: list = []

    def send(self, to, subject, text, html, idempotency_key):
        ConsoleSender.outbox.append({"to": to, "subject": subject, "text": text, "key": idempotency_key})
        return SendResult(ok=True, provider=self.name, message_id=f"console-{uuid.uuid4().hex[:12]}",
                          detail="dry-run: not sent")


class ResendSender(EmailSender):
    name = "resend"

    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        s = get_settings()
        if not s.resend_api_key or not s.email_from:
            raise RuntimeError("RESEND_API_KEY and EMAIL_FROM must be set as secrets")
        self.from_addr = s.email_from
        self.client = httpx.Client(base_url="https://api.resend.com", timeout=30,
                                   headers={"Authorization": f"Bearer {s.resend_api_key}"}, transport=transport)

    def send(self, to, subject, text, html, idempotency_key):
        body = {"from": self.from_addr, "to": [to], "subject": subject, "text": text}
        if html:
            body["html"] = html
        try:
            r = self.client.post("/emails", json=body, headers={"Idempotency-Key": idempotency_key[:256]})
        except httpx.TransportError as e:
            return SendResult(ok=False, provider=self.name, detail=str(e)[:300], retryable=True)
        if r.status_code in (200, 201):
            return SendResult(ok=True, provider=self.name, message_id=(r.json() or {}).get("id"), http_status=r.status_code)
        retryable = r.status_code in (408, 409, 429) or r.status_code >= 500
        return SendResult(ok=False, provider=self.name, http_status=r.status_code, detail=r.text[:300], retryable=retryable)


class SmtpSender(EmailSender):
    name = "smtp"

    def send(self, to, subject, text, html, idempotency_key):
        s = get_settings()
        if not (s.smtp_host and s.email_from):
            return SendResult(ok=False, provider=self.name, detail="SMTP not configured", retryable=False)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.email_from, to, subject
        msg["Message-ID"] = f"<{idempotency_key.replace(':', '.')}.{uuid.uuid4().hex[:8]}@bidradar>"
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password or "")
                smtp.send_message(msg)
        except Exception as e:
            return SendResult(ok=False, provider=self.name, detail=str(e)[:300], retryable=True)
        return SendResult(ok=True, provider=self.name, message_id=msg["Message-ID"])


def get_sender() -> EmailSender:
    p = get_settings().email_provider.lower()
    if p == "resend":
        return ResendSender()
    if p == "smtp":
        return SmtpSender()
    return ConsoleSender()


def verify_resend_webhook(payload: bytes, headers: dict, secret: Optional[str], tolerance: int = 300) -> bool:
    """Resend signs webhooks with Svix: HMAC-SHA256 over '{id}.{timestamp}.{body}' using the base64 secret
    after the 'whsec_' prefix; header 'svix-signature' holds space-separated 'v1,<b64sig>' values."""
    if not secret:
        return False
    msg_id, ts, sigs = headers.get("svix-id"), headers.get("svix-timestamp"), headers.get("svix-signature")
    if not (msg_id and ts and sigs):
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance:
            return False
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + payload, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s.split(",", 1)[1]) for s in sigs.split() if "," in s)
