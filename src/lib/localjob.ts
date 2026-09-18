import { finding, type FindingDefinition } from "./contracts";
import { sha256Text } from "./encoding";
import type { Change, CorrectionReport, Flag } from "./report";
import { applyEdits, type Edit } from "./substitution";

/*
 * The local (browser) job model: an immutable source, an explicit import
 * intent, numbered analyses, proposals bound to a change set, approvals that
 * go stale when their inputs change, and export artifacts with lineage.
 * Pure data and functions; the page and the worker call into this module.
 *
 * Application semantics beyond comparison are not supported locally: there is
 * no baseline generation to publish into, so the only import mode that can run
 * is comparison_only. Other modes are recorded as findings, never applied.
 */

export const PARSER_VERSION = "local-ts/1";
export const RULESET_VERSION = "iso6346-mod11/1; uic-luhn-mod10/1";
export const FINGERPRINT_VERSION = "1";

export type ImportMode = "comparison_only" | "full_snapshot" | "incremental" | "explicit_removal";

export interface ImportIntent {
  mode: ImportMode;
  scope: { kind: "terminal" | "source_fleet" | "location" | "voyage" | "profile_population"; value: string };
  effective_time: string;
  field_update_policy: "absent_means_no_update" | "unknown_blocks";
  trust: boolean;
  owner_policy: "strict" | "lenient";
  format_hint: "auto" | "csv" | "fixed";
  parse_options: { columns?: (string | number)[]; has_header?: boolean; whole_cell?: boolean; ranges?: [number, number][]; header_lines?: number };
}

export function defaultIntent(): ImportIntent {
  return {
    mode: "comparison_only",
    scope: { kind: "source_fleet", value: "" },
    effective_time: new Date().toISOString(),
    field_update_policy: "absent_means_no_update",
    trust: false,
    owner_policy: "strict",
    format_hint: "auto",
    parse_options: {},
  };
}

export interface SourceFile {
  filename: string;
  size_bytes: number;
  sha256: string;
  encoding: string;
  declared_content_type: string;
  received_at: string;
  immutable: true;
}

export interface LocalFinding extends FindingDefinition {
  id: string;
  detail: string;
  location?: string;
}

export type ProposalState = "proposed" | "approved" | "rejected" | "deferred" | "stale" | "applied_to_draft";

export interface Proposal {
  id: string;
  kind: "correction" | "flag";
  raw: string;
  normalized: string;
  candidate: string | null;
  rule_id: string;
  rule_version: string;
  reason: string;
  evidence: string;
  field_context: string;
  confidence_basis: string;
  occurrences: { offset: number; label: string; before: string; after: string }[];
  state: ProposalState;
  decided_at: string | null;
  stale_reason: string | null;
  comment: string;
}

export interface Analysis {
  version: number;
  created_at: string;
  detected_format: string;
  detection_reason: string;
  intent: ImportIntent;
  report: CorrectionReport | null;
  proposals: Proposal[];
  findings: LocalFinding[];
  counts: { physical_lines: number; identifier_occurrences: number; unique_identifiers: number; logical_records: number | null };
  parser_version: string;
  ruleset_version: string;
}

export interface Approval {
  id: string;
  proposal_ids: string[];
  change_set_fingerprint: string;
  approver: string;
  authorization_scope: "draft";
  decided_at: string;
  decision: "approve" | "reject" | "defer";
  state: "approved" | "stale" | "superseded";
  stale_reason: string | null;
  selection_count: number;
}

let findingSeq = 0;
export function makeFinding(code: string, detail: string, location?: string): LocalFinding {
  findingSeq += 1;
  const base = finding(code);
  return { ...base, id: `f${findingSeq}`, detail, ...(location ? { location } : {}) };
}

// A local inspection only ever compares; it never writes to a stored fleet.
// Comparison-only mode is the whole capability here, so it needs no finding and
// a coverage scope is optional. The snapshot, incremental and removal modes do
// change a stored fleet, which the local inspector cannot do; that limitation is
// the one worth surfacing.
export function intentFindings(intent: ImportIntent): LocalFinding[] {
  const out: LocalFinding[] = [];
  if (intent.mode !== "comparison_only") {
    out.push(makeFinding(
      "IMPORT_INTENT_UNRESOLVED",
      "Snapshot, incremental and removal modes change a stored fleet, which needs the private workspace. Here the file is inspected in comparison-only mode: findings and proposals are produced and nothing is applied to a fleet.",
    ));
  }
  return out;
}

export function countLines(text: string): number {
  if (text.length === 0) return 0;
  let n = 0;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) n++;
  return text.endsWith("\n") ? n : n + 1;
}

// One proposal per Change (all occurrences kept in sync) and one per Flag.
export function buildProposals(report: CorrectionReport, intent: ImportIntent, detectedFormat: string): Proposal[] {
  const out: Proposal[] = [];
  let seq = 0;
  const fieldContext = detectedFormat === "txt" ? (intent.trust ? "equipment_id (trusted free text)" : "free_text") : "equipment_id";
  const push = (p: Omit<Proposal, "id" | "state" | "decided_at" | "stale_reason" | "comment">) => {
    seq += 1;
    out.push({ ...p, id: `p${seq}`, state: "proposed", decided_at: null, stale_reason: null, comment: "" });
  };
  for (const c of report.corrected as Change[]) {
    push({
      kind: "correction",
      raw: c.old,
      normalized: c.old,
      candidate: c.new,
      rule_id: c.id_type === "uic" ? "uic/check-digit" : "iso6346/check-digit",
      rule_version: RULESET_VERSION,
      reason: c.reason,
      evidence: "expected check digit for this body",
      field_context: fieldContext,
      confidence_basis: "arithmetic only; the body is assumed correct",
      occurrences: c.occurrences,
    });
  }
  for (const f of report.flagged as Flag[]) {
    push({
      kind: "flag",
      raw: f.eqid,
      normalized: f.eqid,
      candidate: f.suggested_check ? null : null,
      rule_id: "kernel/flag",
      rule_version: RULESET_VERSION,
      reason: f.reason,
      evidence: f.suggested_check ? `suggested check digit ${f.suggested_check}; not applied` : "no candidate",
      field_context: fieldContext,
      confidence_basis: "review required",
      occurrences: f.offset >= 0 ? [{ offset: f.offset, label: `${f.occurrences} occurrence${f.occurrences === 1 ? "" : "s"}`, before: f.eqid, after: f.eqid }] : [],
    });
  }
  return out;
}

export interface ChangeSetInputs {
  source_sha256: string;
  analysis_version: number;
  intent: ImportIntent;
  approved: Proposal[];
}

/** Stable, versioned digest of the effective inputs of a change set. */
export async function changeSetFingerprint(inputs: ChangeSetInputs): Promise<string> {
  const edits = [...inputs.approved]
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map((p) => ({ id: p.id, before: p.raw, after: p.candidate, occurrences: p.occurrences.map((o) => [o.offset, o.label]), rule: `${p.rule_id}@${p.rule_version}` }));
  const material = JSON.stringify({
    v: FINGERPRINT_VERSION,
    source: inputs.source_sha256,
    analysis: inputs.analysis_version,
    parser: PARSER_VERSION,
    mode: inputs.intent.mode,
    scope: inputs.intent.scope,
    trust: inputs.intent.trust,
    owner_policy: inputs.intent.owner_policy,
    edits,
  });
  return sha256Text(material);
}

export interface DecisionResult {
  proposals: Proposal[];
  affected: number;
}

export function decide(proposals: Proposal[], ids: readonly string[], decision: "approve" | "reject" | "defer" | "undo", comment = ""): DecisionResult {
  const wanted = new Set(ids);
  let affected = 0;
  const now = new Date().toISOString();
  const next = proposals.map((p) => {
    if (!wanted.has(p.id)) return p;
    if (p.kind === "flag" && decision === "approve") return p; // a flag has no candidate to approve
    affected += 1;
    const state: ProposalState = decision === "approve" ? "approved" : decision === "reject" ? "rejected" : decision === "defer" ? "deferred" : "proposed";
    return { ...p, state, decided_at: decision === "undo" ? null : now, stale_reason: null, comment: comment || p.comment };
  });
  return { proposals: next, affected };
}

export function markStale(proposals: Proposal[], reason: string): Proposal[] {
  return proposals.map((p) => (p.state === "approved" ? { ...p, state: "stale", stale_reason: reason } : p));
}

/** Only approved corrections are applied; the original is never touched. */
export function applyApproved(text: string, proposals: readonly Proposal[]): { text: string; edits: Edit[] } {
  const edits: Edit[] = [];
  for (const p of proposals) {
    if (p.state !== "approved" || p.kind !== "correction" || !p.candidate) continue;
    for (const occ of p.occurrences) edits.push({ start: occ.offset, oldText: p.raw, newText: p.candidate });
  }
  return { text: applyEdits(text, edits), edits };
}

function csvEscape(v: string | number | null): string {
  const s = v === null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function exceptionsCsv(proposals: readonly Proposal[]): string {
  const rows = [["id", "kind", "state", "raw", "normalized", "candidate", "reason", "location", "stale_reason", "comment"]];
  for (const p of proposals) {
    if (p.state === "approved" || p.state === "applied_to_draft") continue;
    rows.push([p.id, p.kind, p.state, p.raw, p.normalized, p.candidate ?? "", p.reason, p.occurrences.map((o) => o.label).join("; "), p.stale_reason ?? "", p.comment]);
  }
  return "﻿" + rows.map((r) => r.map(csvEscape).join(",")).join("\r\n") + "\r\n";
}

export function ledgerCsv(proposals: readonly Proposal[]): string {
  const rows = [["id", "before", "after", "offset", "label", "rule", "rule_version", "state", "decided_at"]];
  for (const p of proposals) {
    if (p.kind !== "correction") continue;
    for (const o of p.occurrences) rows.push([p.id, p.raw, p.candidate ?? "", String(o.offset), o.label, p.rule_id, p.rule_version, p.state, p.decided_at ?? ""]);
  }
  return "﻿" + rows.map((r) => r.map(csvEscape).join(",")).join("\r\n") + "\r\n";
}

export interface Manifest {
  artifact_version: string;
  source: SourceFile;
  analysis_version: number;
  parser_version: string;
  ruleset_version: string;
  intent: ImportIntent;
  detected_format: string;
  output: { sha256: string; size_bytes: number; encoding: string; output_mode: "surgical"; filename: string };
  change_set: { fingerprint: string; fingerprint_version: string; approved: number; edits_applied: number };
  approvals: Approval[];
  counts: Record<string, number>;
  unresolved: number;
  built_at: string;
  verification: { reparsed: boolean; remaining_corrections_in_output: number; note: string };
}
