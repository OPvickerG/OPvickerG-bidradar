"""Synthetic test data: GeM-shaped listing records, a mock GeM HTTP server and a minimal PDF generator.
Nothing here talks to a real portal."""
from __future__ import annotations

import json
from typing import Dict, List, Optional
from urllib.parse import parse_qs

import httpx

CSRF = "0123456789abcdef0123456789abcdef"


def gem_doc(b_id: int, title: str, end: str = "2026-12-05T15:00:00Z", start: str = "2026-11-18T10:00:00Z",
            number: Optional[str] = None, service: bool = True, bid_type: int = 1, parent_id: Optional[int] = None,
            parent_number: Optional[str] = None, b_status: int = 1, buyer_status: int = 0, quantity: int = 1,
            dept: str = "Department of Military Affairs", ministry: str = "Ministry of Defence") -> Dict:
    """Shape mirrors the Solr documents returned by POST /all-bids-data (values are 1-element lists)."""
    d = {"b_id": [b_id], "b_bid_number": [number or f"GEM/2026/B/{b_id}"], "bbt_title": [title],
         "b_category_name": [title], "b_type": [1 if service else 0], "b_bid_type": [bid_type],
         "b_status": [b_status], "b_buyer_status": [buyer_status], "b_total_quantity": [quantity],
         "ba_official_details_minName": [ministry], "ba_official_details_deptName": [dept],
         "final_start_date_sort": [start], "final_end_date_sort": [end], "b.b_created_by": ["buyer-login-123"]}
    if parent_id:
        d["b_id_parent"] = [parent_id]
        d["b_bid_number_parent"] = [parent_number]
    return d


class MockGem:
    """In-memory stand-in for bidplus.gem.gov.in. Mutate `.docs`, `.corrigenda`, `.results` between scans."""

    def __init__(self):
        self.docs: List[Dict] = []
        self.cancelled: List[Dict] = []
        self.corrigenda: Dict[str, str] = {}
        self.results: Dict[str, str] = {}
        self.pdfs: Dict[str, bytes] = {}
        self.fail_listing = False
        self.calls: List[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/all-bids":
            return httpx.Response(200, text=f"<script>var x = {{'csrf_bd_gem_nk':'{CSRF}'}};</script>")
        if path == "/all-bids-data":
            if self.fail_listing:
                return httpx.Response(503, text="Service Unavailable")
            form = parse_qs(request.content.decode())
            assert form.get("csrf_bd_gem_nk") == [CSRF]
            payload = json.loads(form["payload"][0])
            search = payload["param"]["searchBid"].lower()
            mode = payload["filter"].get("bidStatusType", "ongoing_bids")
            pool = self.cancelled if mode == "cancelled_bids" else self.docs
            if payload["param"].get("searchType") == "exact":
                hits = [d for d in pool if d["b_bid_number"][0].lower() == search]
            else:
                words = search.split()
                hits = [d for d in pool if all(w in d["bbt_title"][0].lower() for w in words)]
            if payload["page"] > 1 or not hits:
                return httpx.Response(404, text="")
            return httpx.Response(200, json={"code": 200, "response": {"response": {"numFound": len(hits),
                                                                                  "docs": json.loads(json.dumps(hits))}}})
        if path.startswith("/public-bid-other-details/"):
            b = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"response": {"corrigendum": b in self.corrigenda, "representation": False}})
        if path.startswith("/bidding/bid/viewCorrigendum/"):
            return httpx.Response(200, text=self.corrigenda.get(path.rsplit("/", 1)[1], ""))
        if path.startswith("/bidding/bid/getBidResultView/"):
            return httpx.Response(200, text=self.results.get(path.rsplit("/", 1)[1], "<html></html>"))
        if path.startswith("/showbidDocument/"):
            b = path.rsplit("/", 1)[1]
            if b in self.pdfs:
                return httpx.Response(200, content=self.pdfs[b], headers={"content-type": "application/pdf"})
            return httpx.Response(404)
        return httpx.Response(404)

    def adapter(self):
        from scanner.source_adapters.base import PoliteClient
        from scanner.source_adapters.gem_bidplus import BASE, GemBidPlusAdapter
        client = PoliteClient("gem_bidplus", BASE, delay=0, max_retries=0, failure_threshold=3,
                              transport=httpx.MockTransport(self.handler))
        return GemBidPlusAdapter(client)


def corrigendum_html(*entries: str) -> str:
    """entries like '20-11-2026 10:00:00|Bid extended to 30-11-2026 15:00:00'"""
    rows = []
    for e in entries:
        when, _, change = e.partition("|")
        rows.append(f"<div>Modified On: {when} {change or 'Download'}</div>")
    return "<html><body>" + "".join(rows) + "</body></html>"


def result_html(prices: List[float], masked: bool = False, statuses: Optional[List[str]] = None) -> str:
    statuses = statuses or ["Qualified"] * len(prices)
    tech = "".join(f"<tr><td>{i + 1}</td><td>{'****' if masked else f'Seller {i + 1}'}</td><td>01-12-2026</td><td>{s}</td></tr>"
                   for i, s in enumerate(statuses))
    fin = "".join(f"<tr><td>{i + 1}</td><td>Seller {i + 1}</td><td>₹ {p:,.2f}</td><td>L{i + 1}</td></tr>"
                  for i, p in enumerate(sorted(prices)))
    return ("<html><table><tr><th>S.No</th><th>Seller Name</th><th>Offered Item</th><th>Participated On</th><th>Status</th></tr>"
            f"{tech}</table><table><tr><th>S.No</th><th>Seller Name</th><th>Total Price</th><th>Rank</th></tr>{fin}</table></html>")


def make_pdf(lines: List[str]) -> bytes:
    """Smallest valid single-page PDF with a Helvetica text layer (one line per text row)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    y, ops = 800, []
    for ln in lines:
        ops.append(f"BT /F1 9 Tf 40 {y} Td ({esc(ln)}) Tj ET")
        y -= 14
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def gem_bid_pdf(number: str = "GEM/2026/B/7000001", end: str = "05-12-2026 15:00:00", value: str = "850000",
                emd: str = "17000", turnover: str = "10 Lakh (s)", years: str = "3 Year", pin: str = "110010",
                item: str = "Repair and Overhauling Service - DG Set; Diesel Generator; Yes; Buyer Premises") -> bytes:
    return make_pdf([
        "Bid Details",
        f"Bid Number: {number}",
        "Dated: 18-11-2026",
        f"Bid End Date/Time {end}",
        "Bid Opening Date/Time 05-12-2026 15:30:00",
        "Bid Offer Validity (From End Date) 90 (Days)",
        "/Ministry/State Name Ministry of Defence /",
        "/Department Name Department of Military Affairs /",
        "/Organisation Name Indian Army /",
        "/Office Name **** /",
        "/Total Quantity 4 /",
        f"/Item Category {item} /",
        "/Contract Period 1 Year(s) /",
        f"Minimum Average Annual Turnover of the bidder (For the last 3 Financial Years) {turnover} /",
        f"Years of Past Experience Required for same/similar service {years}(s) /",
        "Estimated Bid Value " + value,
        "EMD Detail",
        "Required Yes",
        f"EMD Amount {emd}",
        "ePBG Detail",
        "ePBG Percentage(%) 3.00",
        "Duration of ePBG required (Months). 14",
        "Contact the buyer at officer.name@example.gov.in or 9876543210",
        f"/Delivery Days 1 Station Workshop, Delhi Cantt {pin} 4 30 /",
    ])
