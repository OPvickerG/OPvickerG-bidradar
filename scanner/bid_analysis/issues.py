"""Potential issues / missing information for a tender (shown on dashboard and in emails)."""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from core.timeutil import ensure_utc, utcnow


def potential_issues(deadline_at, doc_fields: Optional[Dict], doc_status: Optional[str]) -> List[str]:
    out: List[str] = []
    now = utcnow()
    dl = ensure_utc(deadline_at)
    if dl:
        left = dl - now
        if left < timedelta(0):
            out.append("Submission deadline has passed")
        elif left < timedelta(hours=48):
            out.append(f"Closes in under 48 hours ({int(left.total_seconds() // 3600)} h left)")
        elif left < timedelta(days=5):
            out.append(f"Short window: {left.days} days left")
    f = doc_fields or {}
    t = f.get("min_avg_turnover_inr")
    if t and t >= 5_000_000:
        out.append(f"High turnover requirement: {f.get('min_avg_turnover')}")
    if f.get("years_experience"):
        out.append(f"Requires {f['years_experience']}(s) of past experience")
    docs = (f.get("documents_required") or "").lower()
    if "oem authorization" in docs or "oem authorisation" in docs:
        out.append("OEM authorisation certificate required")
    if f.get("emd_amount") and f["emd_amount"] >= 100000:
        out.append(f"EMD of ₹{f['emd_amount']:,.0f} (check MSE/startup exemption)")
    if f.get("epbg_percent"):
        out.append(f"Performance security {f['epbg_percent']}% on award")
    if f.get("gemarpts_no_category"):
        out.append("Custom/BOQ bid: items have no standard GeM category — read specification files carefully")
    if f.get("buyer_atc_present"):
        out.append("Buyer-specific terms (ATC) attached — conditions may override standard terms")
    if doc_status and doc_status != "extracted":
        out.append(f"Tender document not analysed ({doc_status}) — review the official PDF manually")
    return out


def missing_information(tender_fields: Dict) -> List[str]:
    labels = {"estimated_value": "Estimated value not disclosed", "emd": "EMD not stated / not applicable",
              "contract_duration": "Contract duration not stated", "location": "Delivery location not extracted"}
    return [msg for k, msg in labels.items() if not tender_fields.get(k)]
