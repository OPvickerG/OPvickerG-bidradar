"""GeM BidPlus adapter — public bid listing, bid/RA documents, corrigenda and result pages.

Mechanisms verified in research (Phase 1 & 2, Nov 2026):
  * listing: POST /all-bids-data (form: payload JSON + csrf_bd_gem_nk token embedded in /all-bids page)
      -> Solr-style JSON {response:{response:{numFound, docs:[...]}}}; HTTP 404 means "no results"
  * documents: /showbidDocument/{b_id}, /showradocumentPdf/{b_id}, /showdirectradocumentPdf/{b_id}
  * flags: POST /public-bid-other-details/{b_id} -> {"response":{"corrigendum":bool,"representation":bool}}
  * corrigenda: POST /bidding/bid/viewCorrigendum/{b_id} (HTML)
  * results: GET /bidding/bid/getBidResultView/{b_id} (public HTML: bidders, qualified/disqualified, L1..Ln)
No login, CAPTCHA or OTP is touched. Buyer officials' personal details on result pages are never stored."""
from __future__ import annotations

import html as htmlmod
import json
import re
from html.parser import HTMLParser
from typing import Dict, Iterator, List, Optional, Tuple

from core.timeutil import parse_gem_listing_ts
from .base import NoResults, PoliteClient, RawTender, RefreshResult, SourceAdapter

BASE = "https://bidplus.gem.gov.in"
_CSRF = re.compile(r"csrf_bd_gem_nk['\"]?\s*[:,]\s*['\"]([0-9a-f]{16,})")
_STAGES = ["Not Evaluated", "Technical Evaluation", "Financial Evaluation", "Bid Award"]


def _first(d: dict, k: str):
    v = d.get(k)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def doc_to_raw(d: dict, mode: str = "ongoing_bids") -> RawTender:
    b_id = str(_first(d, "b_id"))
    bid_type = _first(d, "b_bid_type")
    number = _first(d, "b_bid_number")
    is_ra = bid_type in (2, 5)
    parent = _first(d, "b_bid_number_parent")
    parent_id = _first(d, "b_id_parent")
    title = _first(d, "bbt_title") or _first(d, "b_category_name") or number
    desc = _first(d, "bd_category_name") or _first(d, "b_category_name")
    b_status = _first(d, "b_status")
    buyer_status = _first(d, "b_buyer_status") or 0
    if mode == "cancelled_bids" or b_status in (3, 5):
        status, stage = "cancelled", "Cancelled"
    elif mode == "ongoing_bids":
        status, stage = "active", "Open for bidding" if not is_ra else "Reverse auction live/scheduled"
    else:
        stage = _STAGES[buyer_status] if isinstance(buyer_status, int) and buyer_status < 4 else str(buyer_status)
        status = {0: "closed", 1: "in_evaluation", 2: "in_evaluation", 3: "awarded"}.get(buyer_status, "closed")
    if bid_type == 5:
        doc_url = f"{BASE}/showdirectradocumentPdf/{b_id}"
    elif bid_type == 2:
        doc_url = f"{BASE}/showradocumentPdf/{b_id}"
    else:
        doc_url = f"{BASE}/showbidDocument/{b_id}"
    documents = [("ra_document" if is_ra else "bid_document", doc_url)]
    if is_ra and parent_id:
        documents.append(("bid_document", f"{BASE}/showbidDocument/{parent_id}"))
    flags = {k: _first(d, k) for k in ("is_high_value", "is_rc_bid", "ba_is_global_tendering",
                                       "ba_is_limited_tendering", "ba_is_single_packet", "b_is_custom_item",
                                       "b_is_bunch", "b_eval_type", "b_cat_id")}
    # Identity = the procurement: a bid and the RA spawned from it share the parent bid's id.
    identity = str(parent_id) if (is_ra and parent_id) else b_id
    return RawTender(
        source_key="gem_bidplus", external_id=identity, title=str(title)[:1000], bid_number=number if not is_ra else parent,
        ra_number=number if is_ra else None, parent_bid_number=parent,
        description=str(desc)[:4000] if desc else None,
        procurement_type={0: "product", 1: "service"}.get(_first(d, "b_type")),
        ministry=_first(d, "ba_official_details_minName"), department=_first(d, "ba_official_details_deptName"),
        organization=_first(d, "ba_official_details_deptName"),
        published_at=parse_gem_listing_ts(_first(d, "final_start_date_sort")),
        deadline_at=parse_gem_listing_ts(_first(d, "final_end_date_sort")),
        status=status, stage=stage, quantity=_first(d, "b_total_quantity"),
        official_url=f"{BASE}/bidding/bid/getBidResultView/{parent_id or b_id}" if status not in ("active",) else doc_url,
        document_url=doc_url, documents=documents,
        extra={"b_id": b_id, "bid_type": bid_type, "parent_b_id": parent_id, "buyer_status": buyer_status,
               "b_status": b_status, "flags": flags, "listing_mode": mode},
    )


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.stack = []; self.row = None; self.cell = None; self.skip = 0

    def handle_starttag(self, tag, a):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "table":
            self.stack.append([])
        elif tag == "tr" and self.stack:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", htmlmod.unescape(" ".join(self.cell))).strip()); self.cell = None
        elif tag == "tr" and self.row is not None and self.stack:
            if self.row:
                self.stack[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())

    def handle_data(self, d):
        if self.cell is not None and not self.skip:
            self.cell.append(d)


def parse_result_page(raw_html: str) -> Dict:
    """Public result page -> outcome summary. Losing bidders are NOT named (privacy); only counts/prices.
    The published L1 name is kept only when the listing says the bid is awarded (caller decides)."""
    p = _Tables(); p.feed(raw_html)
    tech, fin = [], []
    for tb in p.tables:
        if not tb:
            continue
        hdr = [h.lower() for h in tb[0]]
        if any("seller name" in h for h in hdr) and any("participated" in h for h in hdr):
            for r in tb[1:]:
                d = {hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))}
                tech.append({"status": d.get("status", ""), "masked": d.get("seller name", "").startswith("****")})
        elif any("seller name" in h for h in hdr) and any("price" in h for h in hdr):
            for r in tb[1:]:
                d = {hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))}
                price = re.sub(r"[^\d.]", "", next((v for k, v in d.items() if "price" in k), ""))
                fin.append({"seller": re.sub(r"\s*Under PMA", "", d.get("seller name", "")).strip(),
                            "price": float(price) if price else None,
                            "rank": next((v for k, v in d.items() if "rank" in k), "")[:4]})
    return {"bidders": len(tech) or None,
            "qualified": sum(1 for t in tech if t["status"] == "Qualified") if tech else None,
            "disqualified": sum(1 for t in tech if t["status"] == "Disqualified") if tech else None,
            "names_masked": any(t["masked"] for t in tech),
            "ranks": [[f["rank"], f["price"]] for f in fin],
            "l1_name": next((f["seller"] for f in fin if f["rank"] == "L1"), None)}


def parse_corrigenda(raw_html: str) -> List[Dict]:
    t = re.sub(r"<[^>]+>", " ", raw_html)
    t = re.sub(r"\s+", " ", htmlmod.unescape(t))
    out = []
    for m in re.finditer(r"Modified On:\s*([\d-]+ [\d:]+)\s*(?:Download|(Bid extended to [\d-]+ [\d:]+))?", t):
        out.append({"modified_on": m.group(1), "change": m.group(2) or "document/terms amendment"})
    return out


class GemBidPlusAdapter(SourceAdapter):
    key = "gem_bidplus"
    name = "Government e-Marketplace (GeM) — BidPlus public bid listing"
    base_url = BASE
    default_status = "enabled"
    notes = ("Public listing, documents, corrigenda and result pages. Search 'Contains' over item/category text; "
             "index lag up to ~15 min (per GeM banner). Server refused connections after ~25 min of 2 s-spaced "
             "requests during research, so delays are conservative.")

    def __init__(self, client: Optional[PoliteClient] = None):
        self.http = client or PoliteClient(self.key, BASE)
        self.token: Optional[str] = None

    def _init(self):
        r = self.http.request("GET", "/all-bids")
        m = _CSRF.search(r.text)
        if not m:
            raise RuntimeError("GeM listing token not found — page layout may have changed")
        self.token = m.group(1)

    def _query(self, filt: Dict, search: str = "", stype: str = "fullText", page: int = 1) -> Dict:
        if not self.token:
            self._init()
        f = {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "",
             "byEndDate": {"from": "", "to": ""}, "sort": "Bid-Start-Date-Latest"}
        f.update(filt)
        payload = json.dumps({"page": page, "param": {"searchBid": search, "searchType": stype}, "filter": f})
        r = self.http.request("POST", "/all-bids-data", allow_404=True,
                              data={"payload": payload, "csrf_bd_gem_nk": self.token},
                              headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/all-bids"})
        if r.status_code == 404:
            raise NoResults()
        j = r.json()
        if j.get("code") != 200:
            raise RuntimeError(f"unexpected listing response code {j.get('code')}")
        resp = j["response"]["response"]
        for d in resp.get("docs", []):
            d.pop("b.b_created_by", None)       # buyer login id: personal data, never stored
        return resp

    def queries(self, terms: List[str]) -> List[str]:
        return [t for t in dict.fromkeys(t.strip() for t in terms) if len(t) >= 3]

    def fetch_page(self, query: str, page: int) -> List[RawTender]:
        try:
            resp = self._query({}, search=query, page=page)
        except NoResults:
            return []
        return [doc_to_raw(d, "ongoing_bids") for d in resp.get("docs", [])]

    def fetch_latest(self, page: int) -> List[RawTender]:
        """Newest open bids of every kind (no search term) — used by the all-bids sweep."""
        try:
            resp = self._query({}, search="", page=page)
        except NoResults:
            return []
        return [doc_to_raw(d, "ongoing_bids") for d in resp.get("docs", [])]

    def fetch_inventory(self, page: int):
        """One page of the COMPLETE open-bid list, latest closing date first, plus the list's total size.
        New bids enter near the front and closed bids leave from the end, so during a crawl listings only move to
        later pages: re-reads are possible, skips are not."""
        try:
            resp = self._query({"sort": "Bid-End-Date-Latest"}, search="", page=page)
        except NoResults:
            return [], 0
        return [doc_to_raw(d, "ongoing_bids") for d in resp.get("docs", [])], int(resp.get("numFound") or 0)

    def lookup(self, number: str) -> Optional[RawTender]:
        for mode in ("ongoing_bids", "bidrastatus", "cancelled_bids"):
            try:
                resp = self._query({"bidStatusType": mode, "sort": "Bid-End-Date-Latest"}, search=number, stype="exact")
            except NoResults:
                continue
            hit = [d for d in resp.get("docs", []) if _first(d, "b_bid_number") == number]
            if hit:
                return doc_to_raw(hit[0], mode)
        return None

    def refresh(self, tender) -> RefreshResult:
        number = tender.ra_number or tender.bid_number
        raw = self.lookup(number) if number else None
        if not raw:
            return RefreshResult(found=False)
        res = RefreshResult(found=True, raw=raw)
        b_id = raw.extra["b_id"]
        try:
            r = self.http.request("POST", f"/public-bid-other-details/{b_id}", data={"csrf_bd_gem_nk": self.token})
            flags = (r.json() or {}).get("response") or {}
            res.has_corrigendum = bool(flags.get("corrigendum"))
            res.has_representation = bool(flags.get("representation"))
        except Exception:
            pass
        if res.has_corrigendum:
            try:
                r = self.http.request("POST", f"/bidding/bid/viewCorrigendum/{b_id}", data={"csrf_bd_gem_nk": self.token})
                res.corrigenda = parse_corrigenda(r.text)
            except Exception:
                pass
        if raw.status in ("in_evaluation", "awarded") or (raw.extra.get("buyer_status") or 0) >= 1:
            try:
                target = b_id
                r = self.http.request("GET", f"/bidding/bid/getBidResultView/{target}")
                outcome = parse_result_page(r.text)
                outcome["stage"] = raw.stage
                if raw.status != "awarded":
                    outcome["l1_name"] = None          # publish winner only once awarded
                res.outcome = outcome
            except Exception:
                pass
        return res

    def fetch_bytes(self, url: str) -> bytes:
        return self.http.request("GET", url).content

    def close(self):
        self.http.close()
