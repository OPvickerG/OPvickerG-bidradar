"""Where is the work, how far from home, and is a tender ever wrongly hidden for lack of location?"""
from __future__ import annotations

from core.places import find_places, haversine_km, resolve_home
from database.schema.models import Tender
from scanner.bid_analysis.location import consignee_text, geo_label, locate

HOME = {"name": "Testtown", "lat": 30.36, "lon": 76.80, "radius_km": 250}     # a point in north Haryana


def _t(**kw):
    base = dict(source_key="gem_bidplus", external_id="L1", title="Repair of vehicles", status="active", details={})
    base.update(kw)
    return Tender(**base)


def test_delivery_pin_gives_place_and_distance():
    t = _t(details={"doc_fields": {"consignee_pins": ["133001"]}})
    g = locate(t, HOME)
    assert g["place"] == "Ambala" and g["km"] < 10 and g["near"] == "yes" and g["basis"] == "delivery PIN 133001"


def test_masked_consignee_district_is_read_from_document_text():
    text = ("/Consignees/Reporting Officer and Quantity /S.N o. /Consignee Reporting/Officer /Address /Number of "
            "Products to be Repaired /Additional Requirement 1 ***********South Goa 1 N/A Repair And Overhauling")
    assert "South Goa" in consignee_text(text)
    g = locate(_t(details={"doc_text": text}), HOME)
    assert g["place"] == "South Goa" and g["near"] == "no" and g["km"] > 1400


def test_new_delhi_pin_in_address_is_within_250_km():
    text = "/Consignee Reporting/Officer /Address 1 S P 110067,Some Institute, New Delhi, PIN 110067 1 N/A"
    g = locate(_t(details={"doc_text": text}), HOME)
    assert g["state"] == "Delhi" and 150 < g["km"] < 250 and g["near"] == "yes"


def test_office_and_title_used_when_document_has_no_address():
    g = locate(_t(details={"doc_fields": {"office": "Wcl Nagpur Maharashtra"}}), HOME)
    assert g["place"] == "Nagpur" and g["basis"] == "buying office" and g["near"] == "no"
    g = locate(_t(title="Repair of 3 vehicles at Chandimandir"), HOME)
    assert g["place"] == "Chandimandir" and g["near"] == "yes"


def test_unknown_location_is_never_hidden_and_state_only_is_maybe_when_state_is_near():
    assert locate(_t(), HOME)["near"] == "maybe"
    assert locate(_t(state="Punjab"), HOME)["near"] == "maybe"
    assert locate(_t(state="Kerala"), HOME)["near"] == "no"


def test_nearest_of_several_delivery_points_decides():
    t = _t(details={"doc_fields": {"consignee_pins": ["600001", "160017"]}})
    g = locate(t, HOME)
    assert g["place"] == "Chandigarh" and g["near"] == "yes"


def test_ambiguous_names_need_a_state_hint_and_common_words_do_not_match():
    assert find_places("Krishi Upaj Mandi Samiti") == []
    assert find_places("Bilaspur") == []
    assert find_places("Bilaspur, Himachal Pradesh")[0][3] == "Himachal Pradesh"
    assert find_places("Hamirpur", "Uttar Pradesh")[0][3] == "Uttar Pradesh"


def test_home_lookup_label_and_distance_math():
    assert resolve_home("kurukshetra")[0] == "Kurukshetra"
    assert 230 < haversine_km((28.61, 77.21), (30.73, 76.78)) < 250            # Delhi-Chandigarh ~240 km
    g = locate(_t(details={"doc_fields": {"consignee_pins": ["160017"]}}), HOME)
    assert geo_label(g, "Testtown").startswith("Chandigarh") and "km from Testtown" in geo_label(g, "Testtown")
    assert geo_label({"state": "Punjab"}) == "Punjab (exact place not stated)"
    assert geo_label({}) == ""


def test_alerts_only_for_work_confirmed_near_home(db_url):
    from backend import settings_service as cfg
    from database.session import session_scope
    from notifications.notification_queue.queue import tender_qualifies
    with session_scope() as db:
        cfg.put(db, "geography", {"states": [], "pin_prefixes": [], "home": HOME})
        far = _t(external_id="F", relevance_score=95, best_category="F", details={"geo": {"near": "no"}})
        near = _t(external_id="N", relevance_score=95, best_category="F", details={"geo": {"near": "yes"}})
        unknown = _t(external_id="U", relevance_score=95, best_category="F", details={"geo": {"near": "maybe"}})
        assert tender_qualifies(db, near) and not tender_qualifies(db, far) and not tender_qualifies(db, unknown)


def test_pin_followed_by_comma_is_read():
    text = "/Consignee Reporting/Officer /Address 1 ******133001,Ambala Cantt 1 N/A"
    g = locate(_t(details={"doc_text": text}), HOME)
    assert g["basis"] == "delivery PIN 133001" and g["near"] == "yes"


def test_dist_dash_and_misspelt_district_are_resolved():
    text = "/Consignee Reporting/Officer /Address 1 Office of SDO, Dist - Kurukshtra, 1 N/A"
    g = locate(_t(details={"doc_text": text}), HOME)
    assert g["place"] == "Kurukshetra" and g["near"] == "yes"


def test_unknown_far_district_is_outside_but_unknown_near_district_is_kept():
    far = "/Consignee Reporting/Officer /Address 1 ***********Purulia 1 N/A West Bengal"
    g = locate(_t(details={"doc_text": far}), HOME)
    assert g["near"] == "no" and g["state"] == "West Bengal"
    # every place around home is in the gazetteer, so an unknown named district with no state is far away
    vague = "/Consignee Reporting/Officer /Address 1 ***********Somewhere 1 N/A"
    assert locate(_t(details={"doc_text": vague}), HOME)["near"] == "no"
    # ...but an unknown district in a state that reaches the home area is never hidden
    hr = "/Consignee Reporting/Officer /Address 1 ***********Somewhere 1 N/A Haryana"
    assert locate(_t(details={"doc_text": hr}), HOME)["near"] == "maybe"
    army = "/Consignee Reporting/Officer /Address /Quantity /Delivery Days 1 ***********Rajauri 1 15 Camshaft Bearing"
    assert locate(_t(details={"doc_text": army}), HOME)["near"] == "no"


def test_state_in_consignee_text_beats_buyer_state():
    text = "/Consignee Reporting/Officer /Address 1 *********** HAZARIBAGH. JHARKHAND 1 N/A"
    g = locate(_t(state="Haryana", details={"doc_text": text}), HOME)
    assert g["state"] == "Jharkhand" and g["near"] == "no"


def test_small_radius_keeps_home_state_but_drops_far_state_only_bids():
    small = dict(HOME, radius_km=55)
    assert locate(_t(state="Haryana"), small)["near"] == "maybe"
    assert locate(_t(state="Maharashtra"), small)["near"] == "no"
    g = locate(_t(details={"doc_fields": {"consignee_pins": ["135001"]}}), small)
    assert g["near"] == "yes" and g["km"] <= 55                                   # Yamunanagar ~54 km
