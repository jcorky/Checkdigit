import { describe, expect, it } from "vitest";

import { correct } from "../src/lib/dispatcher";
import {
  applyApproved,
  buildProposals,
  changeSetFingerprint,
  countLines,
  decide,
  defaultIntent,
  exceptionsCsv,
  intentFindings,
  ledgerCsv,
  markStale,
} from "../src/lib/localjob";
import type { CorrectionReport } from "../src/lib/report";

const TEXT = "UNB+UNOA:2+A+B+260101:1200+1'EQD+CN+CSQU3054384+22G1'EQD+CN+APLU9192812+45G1'EQD+CN+APLX1234560'EQD+CN+CSQU3054383'UNZ+1+1'";

function analyse(trust = false) {
  const intent = { ...defaultIntent(), trust, scope: { kind: "source_fleet" as const, value: "demo" } };
  const [det, report] = correct(TEXT, { trust, owner_policy: intent.owner_policy });
  const proposals = buildProposals(report as CorrectionReport, intent, det.fmt);
  return { intent, det, report: report as CorrectionReport, proposals };
}

describe("import intent", () => {
  it("a plain comparison-only inspection raises no import-intent finding", () => {
    expect(intentFindings(defaultIntent())).toEqual([]);
    // a coverage scope is optional for a local comparison
    expect(intentFindings({ ...defaultIntent(), scope: { kind: "terminal", value: "T1" } })).toEqual([]);
  });

  it("modes that would change a stored fleet are recorded, never applied", () => {
    const f = intentFindings({ ...defaultIntent(), mode: "full_snapshot", scope: { kind: "terminal", value: "T1" } });
    expect(f).toHaveLength(1);
    expect(f[0]?.code).toBe("IMPORT_INTENT_UNRESOLVED");
    expect(f[0]?.detail).toContain("comparison-only");
  });
});

describe("proposals carry evidence and context", () => {
  it("one proposal per correction and per flag", () => {
    const { proposals } = analyse();
    expect(proposals.map((p) => [p.kind, p.raw, p.candidate])).toEqual([
      ["correction", "CSQU3054384", "CSQU3054383"],
      ["correction", "APLU9192812", "APLU9192819"],
      ["flag", "APLX1234560", null],
    ]);
    expect(proposals[0]?.evidence).toBe("expected check digit for this body");
    expect(proposals[0]?.field_context).toBe("equipment_id");
    expect(proposals.every((p) => p.state === "proposed")).toBe(true);
  });

  it("building the export twice from the same decisions yields identical edits", () => {
    const { proposals } = analyse(true);
    const approved = decide(proposals, proposals.filter((p) => p.kind === "correction").map((p) => p.id), "approve").proposals;
    const first = applyApproved(TEXT, approved);
    // approving does not change decision state, so a second build sees the same set
    expect(approved.filter((p) => p.kind === "correction").every((p) => p.state === "approved")).toBe(true);
    const second = applyApproved(TEXT, approved);
    expect(second.edits).toEqual(first.edits);
    expect(second.text).toBe(first.text);
    expect(first.text).not.toBe(TEXT);
  });

  it("free text without trust yields flags, not corrections", () => {
    const intent = { ...defaultIntent(), scope: { kind: "source_fleet" as const, value: "x" } };
    const [det, report] = correct("note CSQU3054384 here", { trust: false });
    const proposals = buildProposals(report as CorrectionReport, intent, det.fmt);
    expect(proposals.map((p) => p.kind)).toEqual(["flag"]);
    expect(proposals[0]?.field_context).toBe("free_text");
  });
});

describe("decisions and change-set binding", () => {
  it("approving applies only approved edits and leaves the original untouched", async () => {
    const { proposals, intent } = analyse();
    const { proposals: next, affected } = decide(proposals, ["p1"], "approve");
    expect(affected).toBe(1);
    const { text, edits } = applyApproved(TEXT, next);
    expect(edits).toHaveLength(1);
    expect(text).toContain("CSQU3054383+22G1");
    expect(text).toContain("APLU9192812+45G1");
    expect(TEXT).toContain("CSQU3054384+22G1");
    const fp = await changeSetFingerprint({ source_sha256: "a".repeat(64), analysis_version: 1, intent, approved: next.filter((p) => p.state === "approved") });
    expect(fp).toMatch(/^[0-9a-f]{64}$/);
  });

  it("a flag cannot be approved; it can be deferred or rejected with a comment", () => {
    const { proposals } = analyse();
    expect(decide(proposals, ["p3"], "approve").affected).toBe(0);
    const { proposals: next, affected } = decide(proposals, ["p3"], "defer", "ask the yard");
    expect(affected).toBe(1);
    expect(next[2]?.state).toBe("deferred");
    expect(next[2]?.comment).toBe("ask the yard");
    expect(decide(next, ["p3"], "undo").proposals[2]?.state).toBe("proposed");
  });

  it("the fingerprint changes when the approved set, the intent or the source changes", async () => {
    const { proposals, intent } = analyse();
    const one = decide(proposals, ["p1"], "approve").proposals.filter((p) => p.state === "approved");
    const two = decide(proposals, ["p1", "p2"], "approve").proposals.filter((p) => p.state === "approved");
    const base = { source_sha256: "a".repeat(64), analysis_version: 1, intent };
    const f1 = await changeSetFingerprint({ ...base, approved: one });
    const f2 = await changeSetFingerprint({ ...base, approved: two });
    const f3 = await changeSetFingerprint({ ...base, approved: one, intent: { ...intent, owner_policy: "lenient" } });
    const f4 = await changeSetFingerprint({ ...base, approved: one, source_sha256: "b".repeat(64) });
    const f1again = await changeSetFingerprint({ ...base, approved: one });
    expect(new Set([f1, f2, f3, f4]).size).toBe(4);
    expect(f1again).toBe(f1);
  });

  it("re-analysis marks approvals stale with a reason", () => {
    const { proposals } = analyse();
    const approved = decide(proposals, ["p1", "p2"], "approve").proposals;
    const stale = markStale(approved, "trust changed from off to on");
    expect(stale.filter((p) => p.state === "stale")).toHaveLength(2);
    expect(stale[0]?.stale_reason).toBe("trust changed from off to on");
    expect(applyApproved(TEXT, stale).edits).toEqual([]);
  });

  it("bulk decisions report the exact affected count", () => {
    const { proposals } = analyse();
    const r = decide(proposals, proposals.map((p) => p.id), "approve");
    expect(r.affected).toBe(2);
  });
});

describe("export artifacts", () => {
  it("exceptions and ledger CSVs carry a BOM, CRLF and every unresolved item", () => {
    const { proposals } = analyse();
    const next = decide(proposals, ["p1"], "approve").proposals;
    const exc = exceptionsCsv(next);
    expect(exc.startsWith("﻿id,kind,state")).toBe(true);
    expect(exc.split("\r\n").filter(Boolean)).toHaveLength(3);
    expect(exc).toContain("p2,correction,proposed");
    expect(exc).toContain("p3,flag,proposed");
    const ledger = ledgerCsv(next);
    expect(ledger).toContain("p1,CSQU3054384,CSQU3054383");
    expect(ledger).toContain(",approved,");
  });

  it("counts physical lines like a text editor", () => {
    expect(countLines("")).toBe(0);
    expect(countLines("a")).toBe(1);
    expect(countLines("a\nb")).toBe(2);
    expect(countLines("a\nb\n")).toBe(2);
  });
});
