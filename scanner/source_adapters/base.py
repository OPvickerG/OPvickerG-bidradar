"""Source adapter contract + a polite HTTP client shared by all adapters."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

import httpx

from core.config import get_settings
from core.logs import log

logger = logging.getLogger("bidradar.source")


class SourceUnavailable(Exception):
    """Raised when a source keeps failing; the scan skips it and records the outage."""


class NoResults(Exception):
    pass


@dataclass
class RawTender:
    source_key: str
    external_id: str
    title: str
    bid_number: Optional[str] = None
    ra_number: Optional[str] = None
    parent_bid_number: Optional[str] = None
    description: Optional[str] = None
    procurement_type: Optional[str] = None      # product | service | works | None
    organization: Optional[str] = None
    department: Optional[str] = None
    ministry: Optional[str] = None
    state: Optional[str] = None
    location: Optional[str] = None
    published_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    opening_at: Optional[datetime] = None
    status: str = "active"                       # active | closed | in_evaluation | awarded | cancelled
    stage: Optional[str] = None
    quantity: Optional[float] = None
    official_url: Optional[str] = None
    document_url: Optional[str] = None
    documents: List[Tuple[str, str]] = field(default_factory=list)   # (kind, url)
    extra: Dict = field(default_factory=dict)


@dataclass
class RefreshResult:
    found: bool
    raw: Optional[RawTender] = None
    has_corrigendum: Optional[bool] = None
    has_representation: Optional[bool] = None
    corrigenda: List[Dict] = field(default_factory=list)
    outcome: Optional[Dict] = None          # {"stage":..., "bidders":n, "qualified":n, "ranks":[(rank, price)], "winner": name|None}


class PoliteClient:
    """Throttled HTTP client: minimum delay between requests, retries with exponential backoff + jitter,
    and a per-source circuit breaker."""

    def __init__(self, source_key: str, base_url: str, delay: Optional[float] = None, max_retries: int = 2,
                 failure_threshold: Optional[int] = None, transport: Optional[httpx.BaseTransport] = None):
        s = get_settings()
        self.source_key = source_key
        self.delay = s.request_delay_seconds if delay is None else delay
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold or s.source_failure_threshold
        self.consecutive_failures = 0
        self.requests_made = 0
        self._last = 0.0
        self.client = httpx.Client(base_url=base_url, follow_redirects=True,
                                   timeout=httpx.Timeout(s.request_timeout_seconds, connect=min(15.0, s.request_timeout_seconds)),
                                   headers={"User-Agent": s.user_agent, "Accept-Language": "en-IN,en;q=0.8"},
                                   transport=transport)

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def request(self, method: str, url: str, allow_404: bool = False, **kw) -> httpx.Response:
        if self.consecutive_failures >= self.failure_threshold:
            raise SourceUnavailable(f"{self.source_key}: circuit open after {self.consecutive_failures} failures")
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.requests_made += 1
            try:
                r = self.client.request(method, url, **kw)
                if r.status_code == 404 and allow_404:
                    self.consecutive_failures = 0
                    return r
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                self.consecutive_failures = 0
                return r
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500 \
                        and e.response.status_code not in (408, 429):
                    self.consecutive_failures = 0      # the portal answered: this URL is bad, the source is fine
                    raise
                last_exc = e
                log(logger, logging.WARNING, "request failed", source=self.source_key, url=url,
                    attempt=attempt + 1, error=str(e)[:200])
                if attempt < self.max_retries:
                    time.sleep(min(30.0, (2 ** attempt) * max(self.delay, 1.0)) + random.uniform(0, 1))
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            raise SourceUnavailable(f"{self.source_key}: {last_exc}")
        raise last_exc  # type: ignore[misc]

    def close(self):
        self.client.close()


class SourceAdapter:
    key: str = ""
    name: str = ""
    base_url: str = ""
    default_status: str = "disabled"
    notes: str = ""
    implemented: bool = True

    def queries(self, terms: List[str]) -> List[str]:
        """Search queries to run this scan (keyword sources return the terms; listing-only sources return
        a single catch-all query)."""
        raise NotImplementedError

    def fetch_page(self, query: str, page: int) -> List[RawTender]:
        """One page of results, newest first. Empty list = no more results. The pipeline stops paging a
        query as soon as a page contains nothing new or changed."""
        raise NotImplementedError

    def refresh(self, tender) -> RefreshResult:
        raise NotImplementedError

    def fetch_bytes(self, url: str) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass
