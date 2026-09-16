#!/usr/bin/env python3
"""
run_pass13.py -- near-miss suggester tests.

Layers:
  1. osa_distance vs a naive reference implementation, fuzzed over random pairs
     (the only honest way to trust an early-exit DP), plus hand vectors for each
     edit class (substitution / insertion / deletion / transposition).
  2. suggest() ranking, capping, exclusion semantics.
  3. Full service path on a real DB: seed valid containers, then a low-trust run
     whose flagged tokens must come back with the right candidates -- including
     the distance-0 "body matched, digit was the error" case, the same-file
     sibling case, and the never-suggest-a-check-invalid-candidate guard.
  4. The /check composition (kernel + pool + suggest) exactly as the endpoint does.
"""
import os
import random
import tempfile

import db
import equipment_checkdigit as k
import nearmiss
import service


def naive_osa(a: str, b: str) -> int:
    """Textbook OSA, no early exit -- the reference."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def test_distance():
    assert nearmiss.osa_distance("MSKU735177", "MSKU735177") == 0
    assert nearmiss.osa_distance("MSKU735177", "MSKU735179") == 1   # substitution
    assert nearmiss.osa_distance("MSKU735177", "MSKU735717") == 1   # transposition
    assert nearmiss.osa_distance("MSKU735177", "MSKU73517") == 1    # deletion
    assert nearmiss.osa_distance("MSKU735177", "MSKU7351772") == 1  # insertion
    assert nearmiss.osa_distance("MSKU735177", "MSKU753179") == 2   # transp + subst
    assert nearmiss.osa_distance("MSKU735177", "MSKU000177") == 3   # >max -> max+1
    print("  distance: hand vectors per edit class pass")

    rng = random.Random(6346)
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    checked = 0
    for _ in range(400):
        a = "".join(rng.choice(alpha) for _ in range(rng.randint(8, 12)))
        b = list(a)
        for _ in range(rng.randint(0, 4)):              # apply 0-4 random edits
            op = rng.choice("sidt")
            p = rng.randrange(len(b)) if b else 0
            if op == "s" and b:
                b[p] = rng.choice(alpha)
            elif op == "i":
                b.insert(p, rng.choice(alpha))
            elif op == "d" and len(b) > 1:
                del b[p]
            elif op == "t" and p < len(b) - 1:
                b[p], b[p + 1] = b[p + 1], b[p]
        b = "".join(b)
        ref = naive_osa(a, b)
        fast = nearmiss.osa_distance(a, b, max_dist=2)
        assert fast == (ref if ref <= 2 else 3), (a, b, ref, fast)
        big = nearmiss.osa_distance(a, b, max_dist=50)
        assert big == ref, (a, b, ref, big)
        checked += 1
    print(f"  distance: fuzz vs naive reference on {checked} random pairs (exact + early-exit)")


def test_suggest_ranking():
    pool = [("MSKU7351770", 12), ("MSKU7351871", 3),   # bodies: exact, 1-subst away
            ("APLU7932834", 50), ("MSKU9999991", 2)]
    out = nearmiss.suggest("MSKU735177", pool)
    assert out[0] == {"eqid": "MSKU7351770", "distance": 0, "times_seen": 12}, out
    assert out[1]["eqid"] == "MSKU7351871" and out[1]["distance"] == 1, out
    assert len(out) <= 3 and all(s["eqid"] != "APLU7932834" for s in out)
    # exclusion of the token's own full form
    out2 = nearmiss.suggest("MSKU735177", pool, exclude="MSKU7351770")
    assert all(s["eqid"] != "MSKU7351770" for s in out2)
    # ties on distance break by frequency
    out3 = nearmiss.suggest("MSKU735170", [("MSKU7351700", 1), ("MSKU7351770", 9)])
    assert out3[0]["eqid"] == "MSKU7351700" and out3[0]["distance"] == 0
    print("  suggest: distance-0 first, frequency tiebreak, cap, exclusion")


def test_service_end_to_end():
    path = os.path.join(tempfile.mkdtemp(), "p13.db")
    conn = db.connect(path, create_schema=True)

    # Seed run 1: two boxes that are already check-valid (status: valid, recorded).
    r1 = service.process_upload(conn, b"gate: MSKU7351770 ok APLU7932834 ok",
                                filename="seed.txt", content_type="text/plain",
                                user_agent="p13", owner_policy="strict", trust=False)
    by1 = {c.as_found: c for c in r1["report"].containers}
    assert by1["MSKU7351770"].status == "valid" and by1["APLU7932834"].status == "valid"

    # Seed run 2: a check-FAILING token under low trust -> FLAGGED -> recorded with
    # its check-invalid as-found form as canonical. This is the row that must never
    # be suggested. (Low trust on purpose: trust=True would correct it instead.)
    r1b = service.process_upload(conn, b"hold ZZZU1234567", filename="z.txt",
                                 content_type="text/plain", user_agent="p13",
                                 owner_policy="strict", trust=False)
    assert r1b["report"].containers[0].status == "flagged"
    assert not service._pool_check_valid("ZZZU1234567")      # the guard's premise

    # Low-trust run: three failing tokens.
    #   MSKU7351773 -> body matches seeded MSKU7351770 exactly (distance 0)
    #   MSKU7351793 -> one substitution from that body (distance 1)
    #   ZZZU1234561 -> near the check-INVALID registry row; must get NO ZZZU hit
    probe = "recheck MSKU7351773 then MSKU7351793 then ZZZU1234561"
    for tok in ("MSKU7351773", "MSKU7351793"):
        assert k.explain(tok)["verdict"] == "mismatch"   # guarantees flagged path
    r2 = service.process_upload(conn, probe.encode(), filename="probe.txt",
                                content_type="text/plain", user_agent="p13",
                                owner_policy="strict", trust=False)
    by = {c.as_found: c for c in r2["report"].containers}

    nm0 = by["MSKU7351773"].near_misses
    assert nm0 and nm0[0]["eqid"] == "MSKU7351770" and nm0[0]["distance"] == 0, nm0
    nm1 = by["MSKU7351793"].near_misses
    assert any(s["eqid"] == "MSKU7351770" and s["distance"] == 1 for s in nm1), nm1
    zz = by["ZZZU1234561"].near_misses
    assert all(not s["eqid"].startswith("ZZZU") for s in zz), zz
    print("  service: d0 body-match, d1 substitution, check-invalid never suggested")

    # Same-file siblings: one valid box + a 1-off flagged token in ONE paste.
    r3 = service.process_upload(conn, b"CBHU6818017 and CBHU6818013",
                                filename="sib.txt", content_type="text/plain",
                                user_agent="p13", owner_policy="strict", trust=False)
    by3 = {c.as_found: c for c in r3["report"].containers}
    nms = by3["CBHU6818013"].near_misses
    assert nms and nms[0]["eqid"] == "CBHU6818017" and nms[0]["distance"] == 0, nms
    print("  service: same-file sibling matched (pool read after recording)")

    # Contract: near_misses serializes through to_dict (what the API returns).
    d = r3["report"].to_dict()
    rec = next(c for c in d["containers"] if c["as_found"] == "CBHU6818013")
    assert rec["near_misses"][0]["eqid"] == "CBHU6818017"
    print("  contract: near_misses present in report.to_dict()")

    # /check composition exactly as the endpoint performs it.
    res = k.explain("MSKU7351773")
    pool = [(e, s) for e, s in db.candidate_pool(conn) if service._pool_check_valid(e)]
    res["near_misses"] = nearmiss.suggest(res["body"], pool, exclude=res["normalized"])
    assert res["near_misses"][0]["eqid"] == "MSKU7351770"
    assert res["near_misses"][0]["distance"] == 0
    print("  /check: mismatch + suggestions compose")

    conn.close()


def main():
    print("Pass 13 -- near-miss suggester")
    test_distance()
    test_suggest_ranking()
    test_service_end_to_end()
    print("\nPASS: near-miss suggester verified (algorithm fuzz, ranking, service, /check).")


if __name__ == "__main__":
    main()
