"""CPPP tenders via GeM's public mirror pages (gem.gov.in/cppp and /cppp_state).

Verified Nov 2026: server-rendered table, 10 rows/page, sortable by e-published date (order_by=pdate&type=desc).
The title filter on the mirror did NOT filter results when tested, and dates looked inconsistent, so this
source is flagged 'unreliable': we page newest-first and match locally. Tender detail pages on CPPP itself
need a CAPTCHA, so only listing fields are available (no documents)."""
from __future__ import annotations

import html as htmlmod
import re
from typing import Iterator, List, Optional, Tuple

from core.timeutil import parse_ist_string
from .base import PoliteClient, RawTender, RefreshResult, SourceAdapter

_TID = re.compile(r"(\d{4}_[A-Za-z0-9]+_\d+_\d+)\s*$")


def parse_cppp_rows(raw_html: str, source_key: str) -> List[RawTender]:
    m = re.search(r"(?s)<table[^>]*>(.*?)</table>", raw_html)
    if not m:
        return []
    out = []
    for r in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", m.group(1)):
        cells = [re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"(?s)<td[^>]*>(.*?)</td>", r)]
        if len(cells) < 5:
            continue
        closing, opening, published, title_ref, org = cells[:5]
        tid = _TID.search(title_ref)
        if not tid:
            continue
        title = title_ref.split(" /")[0].strip() or title_ref
        out.append(RawTender(
            source_key=source_key, external_id=tid.group(1), bid_number=tid.group(1), title=title[:1000],
            description=title_ref[:2000], organization=org.split("||")[0][:300] or None,
            department=org.split("||")[1][:300] if "||" in org else None,
            published_at=parse_ist_string(published), deadline_at=parse_ist_string(closing),
            opening_at=parse_ist_string(opening), status="active", stage="Published on CPPP",
            official_url="https://eprocure.gov.in/eprocure/app",   # detail view needs CAPTCHA on CPPP
            extra={"corrigendum": cells[5] if len(cells) > 5 else None, "title_ref": title_ref}))
    return out


class CpppMirrorAdapter(SourceAdapter):
    key = "cppp_gem_mirror"
    name = "CPPP central tenders (via GeM mirror gem.gov.in/cppp)"
    base_url = "https://gem.gov.in"
    path = "/cppp"
    default_status = "unreliable"
    notes = ("Listing only; CPPP detail pages require CAPTCHA (not bypassed). Mirror title filter did not work "
             "in testing, so pages are scanned newest-first and matched locally. Mostly works tenders.")

    def __init__(self, client: Optional[PoliteClient] = None):
        self.http = client or PoliteClient(self.key, self.base_url)

    def queries(self, terms: List[str]) -> List[str]:
        return ["(all, newest first)"]

    def fetch_page(self, query: str, page: int) -> List[RawTender]:
        url = f"{self.path}/{page}?order_by=pdate&type=desc" if page > 1 else f"{self.path}?order_by=pdate&type=desc"
        r = self.http.request("GET", url)
        return parse_cppp_rows(r.text, self.key)

    def refresh(self, tender) -> RefreshResult:
        # No per-tender public endpoint without CAPTCHA; state is refreshed when the row reappears in listings.
        return RefreshResult(found=False)

    def fetch_bytes(self, url: str) -> bytes:
        raise NotImplementedError("CPPP documents require CAPTCHA")

    def close(self):
        self.http.close()


class CpppStateMirrorAdapter(CpppMirrorAdapter):
    key = "cppp_state_gem_mirror"
    name = "CPPP state tenders (via GeM mirror gem.gov.in/cppp_state)"
    path = "/cppp_state"
