"""Source registry: what is supported, what is disabled, and why. Coverage is NOT claimed beyond this list."""
from __future__ import annotations

from typing import Dict, List, Type

from .base import SourceAdapter
from .cppp_mirror import CpppMirrorAdapter, CpppStateMirrorAdapter
from .gem_bidplus import GemBidPlusAdapter


class _Listed(SourceAdapter):
    implemented = False

    def queries(self, terms):
        return []

    def fetch_page(self, query, page):
        return []


def _listed(key: str, name: str, url: str, status: str, notes: str) -> Type[SourceAdapter]:
    return type(f"Listed_{key}", (_Listed,), {"key": key, "name": name, "base_url": url,
                                              "default_status": status, "notes": notes})


ADAPTERS: List[Type[SourceAdapter]] = [
    GemBidPlusAdapter,
    CpppMirrorAdapter,
    CpppStateMirrorAdapter,
    _listed("cppp_eprocure", "CPPP eProcurement System (eprocure.gov.in)", "https://eprocure.gov.in/eprocure/app",
            "requires_captcha", "Active-tender listing and search pages show a CAPTCHA (verified Nov 2026). Not bypassed."),
    _listed("state_gepnic", "State e-procurement portals (NIC GePNIC instances, e.g. Haryana, Punjab, Tamil Nadu)",
            "https://etenders.gov.in", "requires_captcha",
            "Tender lists/search require CAPTCHA on GePNIC portals. Partly covered indirectly by the CPPP state mirror."),
    _listed("ireps", "Indian Railways E-Procurement System (IREPS)", "https://www.ireps.gov.in", "requires_auth",
            "Tender participation and most search features need registration/login."),
    _listed("gem_bidnext", "GeM BidNext (railway bids on GeM)", "https://bidnext.gem.gov.in/bidnext/home",
            "unreliable", "Java widget framework (CTRIA) with session-bound rendering; adapter not implemented."),
    _listed("defence_cpse_portals", "Defence / CPSE own portals (e.g. IOCL, ONGC, SAIL SRM)", "—", "disabled",
            "Many CPSEs also publish on GeM (covered). Their own portals are not integrated."),
]


def adapter_classes() -> Dict[str, Type[SourceAdapter]]:
    return {a.key: a for a in ADAPTERS}
