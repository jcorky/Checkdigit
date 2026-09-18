import { correct, correctWithHint, detectBytes, type Detection } from "../lib/dispatcher";
import { decodeBytes, encodeText, sha256Hex, type Codec } from "../lib/encoding";
import { checkMapping, type MappingCheck, type MappingContract } from "../lib/mapping";
import { buildProposals, countLines, intentFindings, makeFinding, PARSER_VERSION, RULESET_VERSION, type Analysis, type ImportIntent, type LocalFinding } from "../lib/localjob";
import { suggest } from "../lib/nearmiss";
import { applyEdits, type Edit } from "../lib/substitution";
import type { CorrectionReport } from "../lib/report";
import { iso6346CheckDigit } from "../lib/checkdigit";
import {
  countMatching,
  decideMatching,
  decisionsDigest,
  exportCorrected,
  MAX_LARGE_BYTES,
  MemorySink,
  MemoryStore,
  readPage,
  runStream,
  summarize,
  undoMatching,
  WHOLE_FILE_LIMIT,
  writeExceptions,
  writeLedger,
  type ApprovalRecord,
  type Counts,
  type Decision,
  type ExportResult,
  type Filter,
  type PageItem,
  type ProposalStore,
  type Sink,
  type StreamOptions,
  type StreamResult,
  type Summary,
} from "../lib/largejob";
import { blobSource } from "../lib/stream";
import { jobDir, OpfsSink, OpfsStore, opfsAvailable, removeAllJobs, storageEstimate, WritableSink } from "./opfs";

/*
 * Web Worker for the local file inspector. Parsing, correction and splicing
 * run here; the page only renders. Messages are plain data; nothing leaves
 * the browser. Files up to WHOLE_FILE_LIMIT are inspected whole in memory;
 * larger files take the streaming path at the end of this module.
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

export type WorkerRequest =
  | InspectRequest
  | ApplyRequest
  | StreamInspectRequest
  | StreamPageRequest
  | StreamCountRequest
  | StreamSummaryRequest
  | StreamDecideRequest
  | StreamUndoRequest
  | StreamExportRequest
  | StreamCancelRequest
  | StreamCloseRequest;

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

export type WorkerResponse =
  | InspectResponse
  | ApplyResponse
  | ErrorResponse
  | StreamProgressMessage
  | StreamInspectedResponse
  | StreamPageResponse
  | StreamCountResponse
  | StreamSummaryResponse
  | StreamDecidedResponse
  | StreamUndoneResponse
  | StreamExportedResponse
  | StreamAckResponse;

const MAX_LOCAL_BYTES = WHOLE_FILE_LIMIT;

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
    findings.push(makeFinding("RESOURCE_BUDGET_EXCEEDED", `The file is ${bytes.length} bytes; the whole-file inspector handles up to ${MAX_LOCAL_BYTES} bytes in browser memory. Save the text as a file and choose it, so the streaming path (up to ${MAX_LARGE_BYTES} bytes) can read it from disk.`));
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

// ---- streaming path (files above the whole-file limit) -----------------------

export interface StreamInspectRequest {
  type: "stream_inspect";
  id: number;
  file: File;
  options: StreamOptions;
  job_id: string;
}
export interface StreamPageRequest {
  type: "stream_page";
  id: number;
  from: number;
  n: number;
  filter: Filter;
}
export interface StreamCountRequest {
  type: "stream_count";
  id: number;
  filter: Filter;
}
export interface StreamSummaryRequest {
  type: "stream_summary";
  id: number;
}
export interface StreamDecideRequest {
  type: "stream_decide";
  id: number;
  filter: Filter;
  decision: Exclude<Decision, "proposed">;
  expected: number | null;
}
export interface StreamUndoRequest {
  type: "stream_undo";
  id: number;
  filter: Filter;
}
export interface StreamExportRequest {
  type: "stream_export";
  id: number;
  filename: string;
  save_handle: FileSystemFileHandle | null;
}
export interface StreamCancelRequest {
  type: "stream_cancel";
  id: number;
}
export interface StreamCloseRequest {
  type: "stream_close";
  id: number;
}

export interface StreamProgressMessage {
  type: "stream_progress";
  id: number;
  phase: string;
  bytes_done: number;
  bytes_total: number;
  counts: Counts | null;
}
export interface StreamInspectedResponse {
  type: "stream_inspected";
  id: number;
  result: StreamResult;
  storage: "opfs" | "memory";
  summary: Summary;
  head_bytes: number;
  head_sha256: string;
}
export interface StreamPageResponse {
  type: "stream_page";
  id: number;
  items: PageItem[];
  next: number | null;
}
export interface StreamCountResponse {
  type: "stream_count";
  id: number;
  count: number;
  proposals: number;
}
export interface StreamSummaryResponse {
  type: "stream_summary";
  id: number;
  summary: Summary;
  decisions_digest: string;
}
export interface StreamDecidedResponse {
  type: "stream_decided";
  id: number;
  approval: ApprovalRecord;
  summary: Summary;
  decisions_digest: string;
}
export interface StreamUndoneResponse {
  type: "stream_undone";
  id: number;
  count: number;
  summary: Summary;
  decisions_digest: string;
}
export interface StreamExportedResponse {
  type: "stream_exported";
  id: number;
  result: ExportResult;
  corrected: File | null;
  exceptions: File;
  ledger: File;
  exception_rows: number;
  ledger_rows: number;
  decisions_digest: string;
}
export interface StreamAckResponse {
  type: "stream_cancelled" | "stream_closed";
  id: number;
}

/** Without the origin-private file system the store lives in memory, so the file size is capped. */
const MEMORY_FALLBACK_LIMIT = 256 * 1024 * 1024;
const HEAD_DIGEST_BYTES = 1024 * 1024;

interface LargeJob {
  id: string;
  file: File;
  store: ProposalStore;
  dir: FileSystemDirectoryHandle | null;
  result: StreamResult | null;
  signal: { cancelled: boolean };
  storage: "opfs" | "memory";
}

let job: LargeJob | null = null;

// Job data left behind by a closed tab is removed the next time the page opens.
if (opfsAvailable()) void removeAllJobs();

function post(msg: WorkerResponse, transfer: Transferable[] = []): void {
  (self as unknown as Worker).postMessage(msg, transfer);
}

async function closeJob(): Promise<void> {
  if (job) {
    job.signal.cancelled = true;
    if (job.store instanceof OpfsStore) job.store.close();
    job = null;
  }
  if (opfsAvailable()) await removeAllJobs();
}

function requireJob(): LargeJob & { result: StreamResult } {
  if (!job || !job.result) throw new Error("no streamed file is open; inspect a file first");
  return job as LargeJob & { result: StreamResult };
}

async function streamInspect(req: StreamInspectRequest): Promise<StreamInspectedResponse> {
  await closeJob();
  const file = req.file;
  if (file.size > MAX_LARGE_BYTES) throw new Error(`the file is ${file.size} bytes; the streaming inspector handles up to ${MAX_LARGE_BYTES} bytes`);
  let store: ProposalStore;
  let dir: FileSystemDirectoryHandle | null = null;
  let storage: "opfs" | "memory" = "memory";
  if (opfsAvailable()) {
    dir = await jobDir(req.job_id);
    store = await OpfsStore.open(dir);
    storage = "opfs";
  } else {
    if (file.size > MEMORY_FALLBACK_LIMIT) {
      throw new Error(`this browser has no origin-private file system, so proposals would have to stay in memory; without it the streaming inspector handles up to ${MEMORY_FALLBACK_LIMIT} bytes`);
    }
    store = new MemoryStore();
  }
  const current: LargeJob = { id: req.job_id, file, store, dir, result: null, signal: { cancelled: false }, storage };
  job = current;
  const result = await runStream(blobSource(file), store, {
    ...req.options,
    signal: current.signal,
    onProgress: (p) => post({ type: "stream_progress", id: req.id, phase: p.phase, bytes_done: p.counts.bytes_done, bytes_total: p.counts.bytes_total, counts: p.counts }),
  });
  if (job !== current) throw new Error("the job was replaced while streaming");
  current.result = result;
  const headBytes = new Uint8Array(await file.slice(0, Math.min(file.size, HEAD_DIGEST_BYTES)).arrayBuffer());
  return { type: "stream_inspected", id: req.id, result, storage, summary: await summarize(store), head_bytes: headBytes.length, head_sha256: await sha256Hex(headBytes) };
}

async function streamExport(req: StreamExportRequest): Promise<StreamExportedResponse> {
  const j = requireJob();
  j.signal.cancelled = false;
  let sink: Sink;
  let finish: () => Promise<File | null>;
  if (req.save_handle) {
    sink = new WritableSink(req.save_handle);
    finish = async () => null;
  } else if (j.dir) {
    const est = await storageEstimate();
    const need = j.file.size + 64 * 1024 * 1024;
    if (est && est.quota > 0 && est.quota - est.usage < need) {
      throw new Error(`STORAGE_QUOTA: the browser allows ${est.quota - est.usage} more bytes of private storage and the corrected file needs about ${need}; use "Save corrected file as" to write it straight to disk`);
    }
    const s = await OpfsSink.open(j.dir, "corrected.bin");
    sink = s;
    finish = () => s.file();
  } else {
    const m = new MemorySink();
    sink = m;
    finish = async () => new File([m.bytes()], req.filename);
  }
  const result = await exportCorrected(blobSource(j.file), j.result.encoding, j.store, sink, {
    signal: j.signal,
    onProgress: (done, total) => post({ type: "stream_progress", id: req.id, phase: "exporting", bytes_done: done, bytes_total: total, counts: null }),
  });
  const corrected = await finish();
  const textFile = async (name: string, write: (s: Sink) => Promise<number>): Promise<[File, number]> => {
    if (j.dir) {
      const s = await OpfsSink.open(j.dir, name);
      const n = await write(s);
      return [await s.file(), n];
    }
    const m = new MemorySink();
    const n = await write(m);
    return [new File([m.bytes()], name, { type: "text/csv" }), n];
  };
  const [exceptions, exception_rows] = await textFile("exceptions.csv", (s) => writeExceptions(j.store, s));
  const [ledger, ledger_rows] = await textFile("ledger.csv", (s) => writeLedger(j.store, s));
  return { type: "stream_exported", id: req.id, result, corrected, exceptions, ledger, exception_rows, ledger_rows, decisions_digest: await decisionsDigest(j.store) };
}

async function handleStream(req: WorkerRequest): Promise<WorkerResponse | null> {
  switch (req.type) {
    case "stream_inspect":
      return streamInspect(req);
    case "stream_page": {
      const j = requireJob();
      const page = await readPage(j.store, req.from, req.n, req.filter);
      return { type: "stream_page", id: req.id, items: page.items, next: page.next };
    }
    case "stream_count": {
      const j = requireJob();
      const c = await countMatching(j.store, req.filter);
      return { type: "stream_count", id: req.id, count: c.count, proposals: c.proposals };
    }
    case "stream_summary": {
      const j = requireJob();
      return { type: "stream_summary", id: req.id, summary: await summarize(j.store), decisions_digest: await decisionsDigest(j.store) };
    }
    case "stream_decide": {
      const j = requireJob();
      const approval = await decideMatching(j.store, req.filter, req.decision, req.expected);
      return { type: "stream_decided", id: req.id, approval, summary: await summarize(j.store), decisions_digest: await decisionsDigest(j.store) };
    }
    case "stream_undo": {
      const j = requireJob();
      const count = await undoMatching(j.store, req.filter);
      return { type: "stream_undone", id: req.id, count, summary: await summarize(j.store), decisions_digest: await decisionsDigest(j.store) };
    }
    case "stream_export":
      return streamExport(req);
    case "stream_cancel":
      if (job) job.signal.cancelled = true;
      return { type: "stream_cancelled", id: req.id };
    case "stream_close":
      await closeJob();
      return { type: "stream_closed", id: req.id };
    default:
      return null;
  }
}

self.onmessage = async (ev: MessageEvent<WorkerRequest>) => {
  const req = ev.data;
  try {
    if (req.type === "inspect") {
      post(await inspect(req));
    } else if (req.type === "apply") {
      const res = await apply(req);
      post(res, [res.bytes]);
    } else {
      const res = await handleStream(req);
      if (res) post(res);
    }
  } catch (err) {
    post({ type: "error", id: req.id, message: `${(err as Error).name}: ${(err as Error).message}` } satisfies ErrorResponse);
  }
};
