#!/usr/bin/env python3
"""
run_pass24.py -- rail & truck intermodal viewer (X12 404/418 + EDIFACT COPINO).

Proves the intermodal pipeline and -- critically -- that DERIVED positions are
flagged as inferred, never presented as if read from the EDI (the grounded
honesty rule):

  equipment class   X12 element 40 / EDIFACT 8053 classify container/car/chassis
  consist (418)     W2 cars parsed in order; sequence = ordinal (no seq element);
                    loaded/empty from W2-05
  doublestack (404) N7 car + container loop grouped to the parent car; well/
                    platform/tier DERIVED and marked inferred; 53ft on top, 40ft
                    bottom by loading rule
  truck (COPINO)    EQD containers + TDT road mode; 2x20ft -> front/rear inferred
  honesty           every derived placement carries inferred flags; renderer
                    draws the inference banner + 'inf' marks
  svg               valid <svg> for all three views
  service           an X12/EDIFACT rail/truck file through process_upload carries
                    a 'visualization' block with the intermodal scene
"""
import os
import tempfile

import db
import intermodal_render as imr
import intermodal_scene as ims
import service


def _n7(init, num, desc="", length="", iso="", car_type=""):
    """Build an N7 with values at exact element positions (N7-01,02,11,15,22,24)."""
    e = [""] * 25
    e[0] = "N7"; e[1] = init; e[2] = num
    if desc:
        e[11] = desc
    if length:
        e[15] = length
    if iso:
        e[22] = iso
    if car_type:
        e[24] = car_type
    return "*".join(e).rstrip("*")


def _w2(init, num, desc="RR", status="", car_type=""):
    e = [""] * 16
    e[0] = "W2"; e[1] = init; e[2] = num; e[4] = desc
    if status:
        e[5] = status
    if car_type:
        e[15] = car_type
    return "*".join(e).rstrip("*")


ISA = ("ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
       "*260611*1200*U*00501*000000001*0*P*:~GS*RC*S*R*20260611*1200*1*X*005010~")
IEA = "GE*1*1~IEA*1*000000001~"


def test_consist():
    msg = (ISA + "ST*418*0001~BAX*072400*T*139*20260611*1500*ABCD CN*072400~W1*CUT~"
           + _w2("ACFX", "49441", "RR", "W", "C113") + "~W3*1*20260620*CN~"
           + _w2("POTX", "2482", "RR", "L", "C114") + "~"
           + _w2("TTGX", "100823", "RR", "L", "S5") + "~SE*7*0001~" + IEA)
    sc = ims.parse_intermodal(msg)
    assert sc and sc.transport == "rail" and sc.view == "consist"
    assert [c.car_id for c in sc.cars] == ["ACFX49441", "POTX2482", "TTGX100823"]
    assert [c.seq for c in sc.cars] == [1, 2, 3]          # order IS the sequence
    assert sc.cars[0].status == "W" and sc.cars[1].status == "L"
    assert "no explicit sequence" in sc.inference_note.lower() or "segment order" in sc.inference_note.lower()
    svg = imr.render_intermodal(sc)["svg"]
    assert svg.startswith("<svg") and "direction of travel" in svg
    print("  consist: W2 cars in order, seq=ordinal, loaded/empty parsed, banner present")


def test_doublestack():
    msg = (ISA + "ST*404*0001~BX*00*R*PP*ABCD*4567~"
           + _n7("DTTX", "720100", "RR", "", "", "S5") + "~"      # 5-pack well car
           + _n7("TCNU", "1234562", "CN", "4000", "42G1") + "~"   # 40ft intl
           + _n7("EMHU", "4455667", "CN", "5300", "53GP") + "~"   # 53ft domestic
           + _n7("GATU", "7654321", "CN", "2000", "22G1") + "~"   # 20ft
           + "SE*6*0001~" + IEA)
    sc = ims.parse_intermodal(msg)
    assert sc and sc.view == "doublestack"
    assert len(sc.cars) == 1 and sc.cars[0].car_id == "DTTX720100"
    units = sc.cars[0].units
    assert len(units) == 3
    assert units[0].length_ft == 40 and units[1].length_ft == 53 and units[2].length_ft == 20
    # well 1: 40ft bottom, 53ft top (loading rule); all derived
    w1 = {u.tier: u for u in units if u.well == 1}
    assert w1["bottom"].length_ft == 40 and w1["top"].length_ft == 53, \
        {t: u.length_ft for t, u in w1.items()}
    assert all("tier" in u.inferred for u in units)       # nothing read from EDI
    assert "DERIVED" in sc.inference_note
    svg = imr.render_intermodal(sc)["svg"]
    assert "inf" in svg                                   # inferred marks drawn
    print("  doublestack: car+containers grouped; 40 bottom/53 top DERIVED + flagged inferred")


def test_truck_copino():
    msg = ("UNB+UNOA:2+SENDER+RECV+260611:1200+1'UNH+1+COPINO:D:00B:UN:SMDG20'"
           "BGM+104++9'TDT+10++3+++++0865CKL'NAD+MS+HAULIER1'"
           "EQD+CN+OSOU1234578+22G0:102:5+++5'SEL+125689'"
           "EQD+CN+TCLU2345670+22G1:102:5+++5'UNT+8+1'UNZ+1+1'")
    sc = ims.parse_intermodal(msg)
    assert sc and sc.transport == "truck" and sc.view == "chassis"
    assert sc.mode == "road"
    assert [u.chassis_pos for u in sc.units] == ["front", "rear"]
    assert all("chassis_pos" in u.inferred for u in sc.units)
    assert "DERIVED" in sc.inference_note
    svg = imr.render_intermodal(sc)["svg"]
    assert "chassis" in svg and "inferred" in svg
    print("  truck: 2x20ft -> front/rear DERIVED + flagged; road mode from TDT")


def test_single_container_truck():
    msg = ("UNB+UNOA:2+S+R+260611:1200+1'UNH+1+CODECO:D:95B:UN:SMDG20'"
           "BGM+34++9'TDT+1++3'EQD+CN+MSКU0000000+45G1:102:5+++5'"
           "EQD+CH+CHAS123456+++5'UNT+6+1'UNZ+1+1'").replace("К", "K")
    sc = ims.parse_intermodal(msg)
    assert sc and sc.transport == "truck"
    conts = [u for u in sc.units if u.eq_class == "container"]
    assert len(conts) == 1 and conts[0].chassis_pos == "single"
    assert sc.counts["chassis"] == 1                      # chassis recognized, not drawn as cargo
    print("  truck: single 40ft + chassis EQD; chassis classified, position 'single'")


def test_service_wiring():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p24.db"), create_schema=True)
    msg = (ISA + "ST*404*0001~BX*00*R*PP*ABCD*4567~"
           + _n7("DTTX", "720100", "RR", "", "", "S5") + "~"
           + _n7("TCNU", "1234562", "CN", "4000", "42G1") + "~"
           + "SE*4*0001~" + IEA)
    res = service.process_upload(conn, msg.encode(), filename="consist.edi",
                                 content_type="", user_agent="p24", owner_policy="strict")
    assert res["status"] == "processed" and res["detected_format"] == "x12"
    viz = res.get("visualization")
    assert viz and viz["transport"] == "rail" and viz["view"] == "doublestack"
    assert "svg" in viz and viz["svg"].startswith("<svg")
    # EDIFACT COPINO through the service too
    copino = ("UNB+UNOA:2+S+R+260611:1200+1'UNH+1+COPINO:D:00B:UN:SMDG20'"
              "BGM+104++9'TDT+10++3+++++TRUCK01'EQD+CN+OSOU1234578+22G0:102:5+++5'"
              "EQD+CN+TCLU2345670+22G1:102:5+++5'UNT+6+1'UNZ+1+1'")
    res2 = service.process_upload(conn, copino.encode(), filename="gate.edi",
                                  content_type="", user_agent="p24", owner_policy="strict")
    assert res2["status"] == "processed"
    assert res2["visualization"] and res2["visualization"]["transport"] == "truck"
    conn.close()
    print("  service: X12 rail + EDIFACT truck both carry intermodal visualization block")


def main():
    print("Pass 24 -- rail & truck intermodal viewer")
    test_consist()
    test_doublestack()
    test_truck_copino()
    test_single_container_truck()
    test_service_wiring()
    print("\nPASS: rail consist + doublestack + truck chassis verified; inference flagged.")


if __name__ == "__main__":
    main()
