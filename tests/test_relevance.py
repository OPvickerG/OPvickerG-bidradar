

def test_aircraft_ship_and_gun_work_is_out_even_when_filed_under_trucks():
    from scanner.bid_analysis.relevance import score_tender
    for t in ["Repair and Overhauling Service - built up trucks; PAINTING AND MARKING OF MI 17 HELICOPTER; Yes",
              "AIRCRAFT ENGINE OVERHAUL", "Replacement works of Marine Crafts LCM Jhelum",
              "REPAIR OF ROTORY OH 155MM BOFORS"]:
        assert score_tender(t, t).score <= 20, t
    assert score_tender("Repair and Overhauling Service - cars; REPAIR OF AIRCRAFT REFUELLER; Yes",
                        "Repair and Overhauling Service - cars; REPAIR OF AIRCRAFT REFUELLER; Yes").score >= 75


def test_vehicle_hire_leasing_and_insurance_are_not_repair_jobs():
    from scanner.bid_analysis.relevance import score_tender
    for t in ["Custom Bid for Services - Hiring of MUV SUV viz M and M Bolero", "Leasing Service of DG-Set",
              "Monthly Ambulance Service (All Inclusive)", "Motor Insurance Service - Commercial Vehicle",
              "Custom Bid for Services - HIRING OF BUSES"]:
        assert score_tender(t, t).score <= 30, t
    t = "Repair and Overhauling Service - Diesel Generators- Dg Sets (DFM); HIRING OF SERVICES FOR RE-GUTTING"
    assert score_tender(t, t).score >= 50                   # buyer filed it as a repair job


def test_buses_vans_and_minibuses_are_strong_matches():
    from scanner.bid_analysis.relevance import score_tender
    for t in ["Repair of Force Traveller minibus", "Servicing and repair of Maruti Eeco van",
              "REPAIR OF ASHOK LEYLAND BUS", "Customized AMC/CMC for Pre-owned Products - DG Sets 40 KVA; KIRLOSKAR; "
              "CMC; customized amc cmc for pre owned products avionics carrier"]:
        assert score_tender(t, t).score >= 75, t
