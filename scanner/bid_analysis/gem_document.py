"""Field extraction from GeM bid / RA documents (English text produced by document_processing).

Labels are those verified in research Phase 2 across 29 real GeM bid PDFs. Every field is optional:
anything not found is returned as None — never guessed."""
from __future__ import annotations

import re
from typing import Dict, List, Optional


def _rx(s: str, pattern: str, group: int = 1) -> Optional[str]:
    m = re.search(pattern, s)
    return m.group(group).strip() if m else None


def _num(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    v = re.sub(r"[^\d.]", "", v)
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _lakh_to_inr(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    m = re.match(r"([\d.]+)\s*(Lakh|Crore)", v)
    if not m:
        return None
    return float(m.group(1)) * (100000 if m.group(2) == "Lakh" else 10000000)


def parse_gem_bid_text(s: str) -> Dict:
    f: Dict = {}
    f["bid_number"] = _rx(s, r"Bid Number\s*:?\s*(GEM/\d{4}/B/\d+)")
    f["ra_number"] = _rx(s, r"RA Number\s*:?\s*(GEM/\d{4}/R/\d+)")
    f["dated"] = _rx(s, r"Dated\s*:\s*(\d{2}-\d{2}-\d{4})")
    f["bid_end"] = _rx(s, r"Bid End Date/Time\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")
    f["bid_opening"] = _rx(s, r"Bid Opening Date/Time\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")
    f["ra_start"] = _rx(s, r"RA Start Date/Time\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")
    f["ra_end"] = _rx(s, r"RA End Date/Time\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")
    f["offer_validity_days"] = _num(_rx(s, r"Bid Offer Validity \(From End Date\)\s+(\d+)"))
    f["ministry"] = _rx(s, r"/Ministry/State Name\s+(.+?)\s+/")
    f["department"] = _rx(s, r"/Department Name\s+(.+?)\s+/")
    f["organisation"] = _rx(s, r"/Organisation Name\s+(.+?)\s+/")
    office = _rx(s, r"/Office Name\s+(.+?)\s+/")
    f["office"] = None if (office and "*" in office) else office
    f["total_quantity"] = _num(_rx(s, r"/Total Quantity\s+([\d,.]+)"))
    f["item_category"] = _rx(s, r"/Item Category\s+(.{1,400}?)(?: GeMARPTS| /|$)")
    f["contract_period"] = _rx(s, r"/Contract Period\s+(\d+ (?:Year|Month|Day)\(s\)(?: \d+ (?:Month|Day)\(s\))*)")
    f["min_avg_turnover"] = _rx(s, r"Minimum Average Annual Turnover of the bidder \(For the last 3 Financial Years\)\s+([\d.]+ ?(?:Lakh|Crore)\s*\(s\))")
    f["min_avg_turnover_inr"] = _lakh_to_inr(f["min_avg_turnover"])
    f["oem_avg_turnover"] = _rx(s, r"OEM Average Turnover \(Last 3 Years\)\s+([\d.]+ ?(?:Lakh|Crore)\s*\(s\))")
    f["years_experience"] = _rx(s, r"Years? of Past Experience Required[^\d]{0,80}?(\d+ Year)")
    f["past_performance"] = _rx(s, r"/Past Performance\s+(\d+ ?%)")
    f["mse_relaxation"] = _rx(s, r"MSE Relaxation for Years Of Experience(?: and Turnover)?\s+(Yes|No)")
    f["startup_relaxation"] = _rx(s, r"Startup Relaxation for Years Of Experience(?: and Turnover)?\s+(Yes|No)")
    f["documents_required"] = _rx(s, r"Document required from seller\s+(.{1,500}?)(?: \*In case| /|$)")
    f["bid_to_ra"] = _rx(s, r"Bid to RA enabled\s+(Yes|No)")
    f["ra_rule"] = _rx(s, r"RA Qualification Rule\s+([A-Za-z0-9\- %]+?Elimination)")
    f["type_of_bid"] = _rx(s, r"Type of Bid\s+(Two Packet Bid|Single Packet Bid)")
    f["tech_clarification_days"] = _rx(s, r"during technical evaluation\s+(\d+ Days)")
    f["inspection_required"] = _rx(s, r"Inspection Required \(By Empanelled Inspection Authority\s*/Agencies pre-registered with GeM\)\s+(Yes|No)")
    f["estimated_value"] = _num(_rx(s, r"Estimated Bid Value(?: in INR \(Inclusive of all taxes\))?\s+([\d,]+)"))
    f["evaluation_method"] = _rx(s, r"Evaluation Method\s+([A-Za-z ]+?evaluation)")
    f["emd_amount"] = _num(_rx(s, r"EMD Amount\s+([\d,]+)"))
    emd_req = _rx(s, r"EMD Detail.{0,80}?Required\s+(Yes|No)")
    f["emd_required"] = "Yes" if f["emd_amount"] else emd_req
    f["epbg_percent"] = _num(_rx(s, r"ePBG Percentage\(%\)\s+([\d.]+)"))
    f["epbg_months"] = _num(_rx(s, r"Duration of ePBG required \(Months\)\.?\s+(\d+)"))
    f["epbg_required"] = "Yes" if f["epbg_percent"] else _rx(s, r"ePBG Detail.{0,80}?Required\s+(Yes|No)")
    f["mii_preference"] = _rx(s, r"MII (?:Purchase Preference|Compliance)\s*/?MII (?:Purchase Preference|Compliance)\s+(Yes|No)")
    f["mse_preference"] = _rx(s, r"MSE Purchase Preference\s*/?MSE Purchase Preference\s+(Yes|No)")
    f["mse_margin_pct"] = _num(_rx(s, r"L1\+X%\s+(\d+)"))
    f["auto_extension_days"] = _num(_rx(s, r"would be auto\s*-?\s*extended\s+(\d+)"))
    f["pre_bid"] = _rx(s, r"Pre-Bid Date and Time\s*/?\s*Pre-Bid Venue\s+(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")
    f["arbitration"] = _rx(s, r"Arbitration Clause\s+(Yes|No)")
    f["price_breakup_required"] = _rx(s, r"Financial Document Indicating Price Breakup Required\s+(Yes|No)")
    f["gemarpts_no_category"] = "Category not available on GeM" in s
    f["buyer_atc_present"] = "Buyer Added Bid Specific" in s
    rows = re.findall(r"/Delivery Days\s+1\s+.{0,220}?\s(\d+)\s(\d+)\s", s)
    strict = re.findall(r"/Delivery Days\s+1\s+.{0,220}?\s(\d+)\s(\d+)\s*/", s)
    loose_days = sorted({int(d) for _, d in rows})
    strict_days = sorted({int(d) for _, d in strict})
    # Only report a delivery period when both readings of the consignee table agree (never guess).
    f["delivery_days"] = loose_days if loose_days and (not strict_days or strict_days == loose_days) else None
    pins = re.findall(r"\b([1-8]\d{5})\b", " ".join(re.findall(r"/Delivery Days\s+1\s+(.{0,220}?)\s\d+\s\d+\s", s)))
    if not pins:            # fallback: PIN codes printed in the consignee/delivery section
        i = s.find("/Delivery Days")
        if i >= 0:
            pins = re.findall(r"(?<![\d.,])([1-8]\d{5})(?![\d.,])", s[i:i + 1500])
    f["consignee_pins"] = sorted(set(pins)) or None
    return f


def eligibility_summary(f: Dict) -> List[str]:
    out = []
    if f.get("min_avg_turnover"):
        out.append(f"Minimum average annual turnover (last 3 FY): {f['min_avg_turnover']}")
    if f.get("oem_avg_turnover"):
        out.append(f"OEM average turnover: {f['oem_avg_turnover']}")
    if f.get("years_experience"):
        out.append(f"Past experience required: {f['years_experience']}(s)")
    if f.get("past_performance"):
        out.append(f"Past performance: {f['past_performance']} of bid quantity")
    if f.get("documents_required"):
        out.append(f"Documents required: {f['documents_required']}")
    if f.get("mse_relaxation") == "Yes":
        out.append("MSE relaxation on experience/turnover available")
    if f.get("startup_relaxation") == "Yes":
        out.append("Startup relaxation on experience available")
    if f.get("mse_preference") == "Yes":
        out.append("MSE purchase preference applies" + (f" (L1+{int(f['mse_margin_pct'])}%)" if f.get("mse_margin_pct") else ""))
    if f.get("mii_preference") == "Yes":
        out.append("Make in India (MII) purchase preference / compliance applies")
    return out


def requirements_summary(f: Dict) -> List[str]:
    out = []
    if f.get("type_of_bid"):
        out.append(f["type_of_bid"])
    if f.get("evaluation_method"):
        out.append(f"Evaluation: {f['evaluation_method']}")
    if f.get("bid_to_ra"):
        out.append(f"Reverse auction after bid: {f['bid_to_ra']}" + (f" ({f['ra_rule']})" if f.get("ra_rule") else ""))
    if f.get("emd_required") == "Yes" and f.get("emd_amount"):
        out.append(f"EMD: ₹{f['emd_amount']:,.0f} (MSE/Startup exemptions per GeM GTC)")
    if f.get("epbg_percent"):
        out.append(f"Performance security (ePBG): {f['epbg_percent']}%" + (f" for {int(f['epbg_months'])} months" if f.get("epbg_months") else ""))
    if f.get("inspection_required") == "Yes":
        out.append("Third-party inspection required")
    if f.get("delivery_days"):
        out.append("Delivery/completion period: " + ", ".join(str(d) for d in f["delivery_days"]) + " days")
    if f.get("contract_period"):
        out.append(f"Contract period: {f['contract_period']}")
    if f.get("pre_bid"):
        out.append(f"Pre-bid meeting: {f['pre_bid']} IST")
    if f.get("price_breakup_required") == "Yes":
        out.append("Price break-up document required")
    if f.get("buyer_atc_present"):
        out.append("Buyer added specific terms (ATC) — read the attached ATC file")
    return out
