import { correct, correctWithHint, detectBytes, type Detection } from "../lib/dispatcher";
import { decodeBytes, encodeText, sha256Hex, type Codec } from "../lib/encoding";
import { checkMapping, type MappingCheck, type MappingContract } from "../lib/mapping";
import { buildProposals, countLines, intentFindings, makeFinding, PARSER_VERSION, RULESET_VERSION, type Analysis, type ImportIntent, type LocalFinding } from "../lib/localjob";
import { suggest } from "../lib/nearmiss";
import { applyEdits, type Edit } from "../lib/substitution";
import type { CorrectionReport } from "../lib/report";
import { iso6346CheckDigit } from "../lib/checkdigit";

/*
 * Web Worker for the local file inspector. Parsing, correction and splicing
 * run here; the page only renders. Messages are plain data; nothing leaves
 * the browser.
 */

export interface InspectRequest {
  type: "inspect";
  id: number;
  bytes: ArrayBuffer;
  filename: string;
  content_type: string;
  intent: ImportIntent;
  mapping: MappingContract | null;
  analysis_version: number;
}

export interface ApplyRequest {
  type: "apply";
  id: number;
  text: string;
  edits: Edit[];
  encoding: Codec;
}

export type WorkerRequest = InspectRequest | ApplyRequest;

export interface InspectResponse {
  type: "inspected";
  id: number;
  sha256: string;
  encoding: Codec | null;
  text: string | null;
  analysis: Analysis;
  mapping: MappingCheck | null;
}

export interface ApplyResponse {
  type: "applied";
  id: number;
  text: string;
  bytes: ArrayBuffer;
  sha256: string;
  reparsed: { corrected: number; flagged: number; valid: number } | null;
}

export interface ErrorResponse {
  type: "error";
  id: number;
  message: string;
}

export type WorkerResponse = InspectResponse | ApplyResponse | ErrorResponse;

const MAX_LOCAL_BYTES = 5 * 1024 * 1024;

function validPool(report: CorrectionReport): [string, number][] {
  const pool = new Map<string, number>();
  for (const c of report.containers) {
    const eqid = c.canonical;
    if (eqid.length !== 11 || !/^[A-Z]{4}[0-9]{7}$/.test(eqid)) continue;
    if (c.status !== "valid" && c.status !== "corrected") continue;
    try {
      if (iso6346CheckDigit(eqid.slice(0, 10)) !== Number(eqid[10])) continue;
    } catch {
      continue;
    }
    pool.set(eqid, (pool.get(eqid) ?? 0) + c.occurrences);
  }
  return [...pool.entries()];
}

function attachNearMisses(report: CorrectionReport): void {
  const pool = validPool(report);
  if (!pool.length) return;
  for (const c of report.containers) {
    if (c.status !== "flagged" && c.status !== "invalid_structure") continue;
    const m = /^([A-Z]{4}[0-9]{6})[0-9]?$/.exec(c.normalized);
    if (!m) continue;
    c.near_misses = suggest(m[1] as string, pool, { exclude: c.normalized }).map((s) => ({
      ...s,
      provenance: { source: "same_file", pool: "check_valid_bodies_in_this_file", retrieval: "scan_of_this_file_only" },
    }));
  }
}

async function inspect(req: InspectRequest): Promise<InspectResponse> {
  const bytes = new Uint8Array(req.bytes);
  const sha256 = await sha256Hex(bytes);
  const findings: LocalFinding[] = intentFindings(req.intent);
  const base = {
    version: req.analysis_version,
    created_at: new Date().toISOString(),
    intent: req.intent,
    parser_version: PARSER_VERSION,
    ruleset_version: RULESET_VERSION,
  };
  if (bytes.length > MAX_LOCAL_BYTES) {
    findings.push(makeFinding("RESOURCE_BUDGET_EXCEEDED", `The file is ${bytes.length} bytes; the local inspector handles up to ${MAX_LOCAL_BYTES} bytes in browser memory. Larger files need the private workspace path.`));
    return {
      type: "inspected", id: req.id, sha256, encoding: null, text: null, mapping: null,
      analysis: { ...base, detected_format: "unsupported", detection_reason: "too large for local processing", report: null, proposals: [], findings, counts: { physical_lines: 0, identifier_occurrences: 0, unique_identifiers: 0, logical_records: null } },
    };
  }
  const binary = detectBytes(bytes);
  if (binary && binary.fmt !== "xlsx") {
    findings.push(makeFinding("UNSUPPORTED_INPUT", binary.reason));
    return {
      type: "inspected", id: req.id, sha256, encoding: null, text: null, mapping: null,
      analysis: { ...base, detected_format: binary.fmt, detection_reason: binary.reason, report: null, proposals: [], findings, counts: { physical_lines: 0, identifier_occurrences: 0, unique_identifiers: 0, logical_records: null } },
    };
  }
  if (binary && binary.fmt === "xlsx") {
    findings.push(makeFinding("UNSUPPORTED_INPUT", "Excel workbooks are handled by the Python service, not by the local inspector yet. Export the sheet as CSV to inspect it here."));
    return {
      type: "inspected", id: req.id, sha256, encoding: null, text: null, mapping: null,
      analysis: { ...base, detected_format: "xlsx", detection_reason: "workbook; local port pending", report: null, proposals: [], findings, counts: { physical_lines: 0, identifier_occurrences: 0, unique_identifiers: 0, logical_records: null } },
    };
  }
  const { text, encoding } = decodeBytes(bytes);
  let det: Detection;
  let report: CorrectionReport | null;
  let mapping: MappingCheck | null = null;
  const options = { trust: req.intent.trust, owner_policy: req.intent.owner_policy };
  if (req.intent.format_hint === "csv") {
    if (req.mapping) {
      mapping = checkMapping(text, req.mapping);
      findings.push(...mapping.findings);
    }
    if (mapping && mapping.blocked) {
      det = { fmt: "csv", reason: "mapping blocked" };
      report = null;
    } else {
      [det, report] = correctWithHint(text, "csv", req.intent.parse_options as never, options);
    }
  } else if (req.intent.format_hint === "fixed") {
    [det, report] = correctWithHint(text, "fixed", req.intent.parse_options as never, options);
  } else {
    try {
      [det, report] = correct(text, options);
    } catch (err) {
      det = { fmt: "unsupported", reason: `${(err as Error).name}: ${(err as Error).message}` };
      report = null;
    }
  }
  if (report === null && det.fmt === "unsupported") findings.push(makeFinding("UNSUPPORTED_INPUT", det.reason));
  let proposals = report ? buildProposals(report, req.intent, det.fmt) : [];
  if (report) attachNearMisses(report);
  const counts = {
    physical_lines: countLines(text),
    identifier_occurrences: report ? report.containers.reduce((n, c) => n + c.occurrences, 0) : 0,
    unique_identifiers: report ? report.total_containers : 0,
    logical_records: mapping ? mapping.rows : null,
  };
  if (det.fmt === "txt" && report && !req.intent.trust && report.flagged.length) {
    findings.push(makeFinding("CONTEXT_AMBIGUOUS", `${report.flagged.length} number${report.flagged.length === 1 ? "" : "s"} found in free text without a declared equipment field. Turn on "treat as declared equipment IDs" only if this file is a container list.`));
    proposals = proposals.map((p) => p);
  }
  return {
    type: "inspected", id: req.id, sha256, encoding, text, mapping,
    analysis: { ...base, detected_format: det.fmt, detection_reason: det.reason, report, proposals, findings, counts },
  };
}

async function apply(req: ApplyRequest): Promise<ApplyResponse> {
  const text = applyEdits(req.text, req.edits);
  const bytes = encodeText(text, req.encoding);
  const sha256 = await sha256Hex(bytes);
  let reparsed: ApplyResponse["reparsed"] = null;
  try {
    const [, rep] = correct(text, { trust: true });
    if (rep) reparsed = { corrected: rep.corrected.length, flagged: rep.flagged.length, valid: rep.valid };
  } catch {
    reparsed = null;
  }
  return { type: "applied", id: req.id, text, bytes: bytes.buffer as ArrayBuffer, sha256, reparsed };
}

self.onmessage = async (ev: MessageEvent<WorkerRequest>) => {
  const req = ev.data;
  try {
    if (req.type === "inspect") {
      const res = await inspect(req);
      (self as unknown as Worker).postMessage(res);
    } else if (req.type === "apply") {
      const res = await apply(req);
      (self as unknown as Worker).postMessage(res, [res.bytes]);
    }
  } catch (err) {
    (self as unknown as Worker).postMessage({ type: "error", id: req.id, message: `${(err as Error).name}: ${(err as Error).message}` } satisfies ErrorResponse);
  }
};
