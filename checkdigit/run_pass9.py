"""
run_pass9.py
============
Demonstrates the enrichment layer end-to-end (stdlib only, no network):

  1. Load the (seed) BIC owner-code registry.
  2. Process the messy txtcontainers.txt with NO registry -> low-trust: failing
     check digits are FLAGGED.
  3. Process the SAME file WITH the registry -> owners are corroborated, so the
     failing check digits are CORRECTED instead (free text, no `trust` needed).
  4. Show that an UNregistered owner stays flagged even with the registry (the
     corroboration gate holds).
  5. Show container_enrichment populated for the ingested containers.
"""
import os
import tempfile
from collections import Counter

import db
import service
from enrichment import load_owner_registry, EnrichmentService

TXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "txtcontainers.txt")
SEED = os.path.join(os.path.dirname(__file__), "owner_registry.seed.csv")


def summarize(res):
    s = res["summary"]
    return f"corrected={s['corrected']} flagged={s['flagged']} valid={s['valid']} containers={s['containers']}"


def main():
    registry = load_owner_registry(SEED)
    svc = EnrichmentService(registry=registry)
    print(f"=== owner registry (seed) ===")
    print(f"  registered prefixes: {sorted(registry.prefixes)}")
    print(f"  lookup MSKU8485186 -> {registry.owner_of('MSKU8485186').company or '(name unverified)'}")
    print(f"  lookup CBHU6818017 -> {registry.owner_of('CBHU6818017').company or '(name unverified)'}")
    print()

    content = open(TXT, "rb").read()

    # (2) no registry -> flags
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "a.db"))
    bare = service.process_upload(conn, content, filename="txtcontainers.txt", user_agent="p9")
    print("=== txtcontainers.txt, NO registry (low-trust) ===")
    print(f"  {summarize(bare)}")

    # (3) with registry -> corroborated corrections
    conn2 = db.connect(os.path.join(tempfile.mkdtemp(), "b.db"))
    enr = service.process_upload(conn2, content, filename="txtcontainers.txt",
                                 user_agent="p9", enrichment=svc)
    print("=== txtcontainers.txt, WITH registry (owner corroboration) ===")
    print(f"  {summarize(enr)}")
    assert bare["summary"]["flagged"] == 38 and bare["summary"]["corrected"] == 0, bare["summary"]
    assert enr["summary"]["corrected"] == 38 and enr["summary"]["flagged"] == 0, enr["summary"]

    # reason text should cite corroboration
    reason = enr["report"].corrected[0].reason
    print(f"  sample reason: {reason}")
    assert "corroborated" in reason

    # (4) gate: an unregistered owner stays flagged even with the registry
    print("\n=== gate: registered vs unregistered owner (.txt, with registry) ===")
    mixed = b"MSKU8485186\nZZZU1234560\n"     # MSK registered (bad check); ZZZ not registered
    conn3 = db.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
    gate = service.process_upload(conn3, mixed, filename="mixed.txt",
                                  user_agent="p9", enrichment=svc)
    rep = gate["report"]
    corrected_eqs = {c.old for c in rep.corrected}
    flagged_eqs = {f.eqid for f in rep.flagged}
    print(f"  corrected: {sorted(corrected_eqs)}")
    print(f"  flagged  : {sorted(flagged_eqs)}")
    assert "MSKU8485186" in corrected_eqs, "registered owner should correct"
    assert any(f.startswith("ZZZU") for f in flagged_eqs), "unregistered owner should stay flagged"

    # (5) enrichment persisted for the ingested (corrected) containers
    print("\n=== container_enrichment (from the WITH-registry run) ===")
    rows = conn2.execute(
        """SELECT eqid, owner_name, source FROM container_enrichment
           ORDER BY (owner_name IS NULL), eqid LIMIT 6""").fetchall()
    for r in rows:
        print(f"  {r['eqid']:<13} owner={r['owner_name'] or '(unverified)':<22} source={r['source']}")
    n = conn2.execute("SELECT COUNT(*) AS n FROM container_enrichment").fetchone()["n"]
    named = conn2.execute("SELECT COUNT(*) AS n FROM container_enrichment WHERE owner_name IS NOT NULL").fetchone()["n"]
    print(f"  total enriched: {n}  (with a verified owner name: {named})")
    assert n > 0

    print("\nPASS: corroboration corrects registered owners, gate holds for unregistered, enrichment persisted.")


if __name__ == "__main__":
    main()
