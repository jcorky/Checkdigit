/*
 * The large-file job: a streaming inspector whose observations live in a
 * store (memory for tests and small files, the origin-private file system in
 * the browser) instead of the JavaScript heap, so a file is bounded by disk
 * and time rather than by tab memory.
 *
 * One pass streams the file, evaluates every identifier with the kernel,
 * counts valid rows and stores every proposal (corrected), review item
 * (flagged, invalid, missing) as a fixed 64-byte record. Decisions are one
 * byte per stored record. Export streams the file again, splices approved
 * candidates at their recorded offsets after verifying the raw span, and
 * writes the result through a sink, never holding the output in memory.
 *
 * Mirrors checkdigit/workspace/ingest.py: mapped CSV columns, plain text
 * tokens, EDIFACT EQD+CN, X12 N7 (check digit slot) and N9*EQ, container XML
 * id-bearing attributes.
 */

import { correctIdentifier, correctX12Equipment, FieldContext, Status, type CorrectionResult } from "./checkdigit";
import type { Codec } from "./encoding";
import { CodecError, csvRecords, delimitedSegments, lineRecords, textChunks, xmlStartTags, type ByteSource } from "./stream";

export const LARGE_PARSER_VERSION = "local-stream-ts/1";
export const RECORD_BYTES = 64;
export const MAX_LARGE_BYTES = 5 * 1024 * 1024 * 1024;
/** Files up to this size are inspected whole in memory; larger ones take the streaming path. */
export const WHOLE_FILE_LIMIT = 5 * 1024 * 1024;

export type Kind = "corrected" | "flagged" | "invalid_structure" | "missing";
export const KIND_CODE: Record<Kind, number> = { corrected: 1, flagged: 2, invalid_structure: 3, missing: 4 };
export const KIND_NAME: Kind[] = ["corrected", "corrected", "flagged", "invalid_structure", "missing"];
export type Decision = "proposed" | "approved" | "rejected" | "deferred";
export const DECISION_CODE: Record<Decision, number> = { proposed: 0, approved: 1, rejected: 2, deferred: 3 };
export const DECISION_NAME: Decision[] = ["proposed", "approved", "rejected", "deferred"];

export interface StoredRecord {
  index: number;
  offset: number;
  record_no: number;
  kind: Kind;
  scheme: string;
  raw: string;
  raw_truncated: boolean;
  candidate: string | null;
  x12_slot: boolean;
  location: string;
}

const SCHEMES = ["unknown", "iso6346", "ilu", "uic", "size_type"];
const RAW_MAX = 24;
const CAND_MAX = 12;

export function encodeRecord(view: DataView, at: number, r: Omit<StoredRecord, "index" | "location">): void {
  view.setFloat64(at, r.offset);
  view.setUint32(at + 8, r.record_no);
  view.setUint8(at + 12, KIND_CODE[r.kind]);
  view.setUint8(at + 13, Math.max(0, SCHEMES.indexOf(r.scheme)));
  const raw = r.raw.slice(0, RAW_MAX);
  view.setUint8(at + 14, raw.length);
  const cand = (r.candidate ?? "").slice(0, CAND_MAX);
  view.setUint8(at + 15, r.candidate === null ? 255 : cand.length);
  let flags = 0;
  if (r.x12_slot) flags |= 1;
  if (r.raw.length > RAW_MAX || r.raw_truncated) flags |= 2;
  for (let i = 0; i < RAW_MAX; i++) view.setUint16(at + 16 + i * 2, i < raw.length ? raw.charCodeAt(i) : 0);
  for (let i = 0; i < CAND_MAX; i++) view.setUint8(at + 64 - CAND_MAX - 1 + i, i < cand.length ? cand.charCodeAt(i) & 0xff : 0);
  view.setUint8(at + 63, flags);
}

export function decodeRecord(view: DataView, at: number, index: number): StoredRecord {
  const offset = view.getFloat64(at);
  const record_no = view.getUint32(at + 8);
  const kind = KIND_NAME[view.getUint8(at + 12)] ?? "corrected";
  const scheme = SCHEMES[view.getUint8(at + 13)] ?? "unknown";
  const rawLen = view.getUint8(at + 14);
  const candLen = view.getUint8(at + 15);
  const flags = view.getUint8(at + 63);
  let raw = "";
  for (let i = 0; i < rawLen; i++) raw += String.fromCharCode(view.getUint16(at + 16 + i * 2));
  let candidate: string | null = null;
  if (candLen !== 255) {
    candidate = "";
    for (let i = 0; i < candLen; i++) candidate += String.fromCharCode(view.getUint8(at + 64 - CAND_MAX - 1 + i));
  }
  return { index, offset, record_no, kind, scheme, raw, raw_truncated: (flags & 2) !== 0, candidate, x12_slot: (flags & 1) !== 0,
    location: `record ${record_no + 1}` };
}

/** Where stored records and decisions live. */
export interface ProposalStore {
  append(bytes: Uint8Array): Promise<void>;
  flush(): Promise<void>;
  readonly count: number;
  read(index: number, n: number): Promise<Uint8Array>;
  decisions(): Uint8Array;
  saveDecisions(): Promise<void>;
  reset(): Promise<void>;
}

export class MemoryStore implements ProposalStore {
  private parts: Uint8Array[] = [];
  private total = 0;
  private dec = new Uint8Array(0);

  get count(): number {
    return this.total / RECORD_BYTES;
  }

  async append(bytes: Uint8Array): Promise<void> {
    this.parts.push(bytes.slice());
    this.total += bytes.length;
  }

  async flush(): Promise<void> {
    if (this.parts.length > 1) {
      const all = new Uint8Array(this.total);
      let at = 0;
      for (const p of this.parts) {
        all.set(p, at);
        at += p.length;
      }
      this.parts = [all];
    }
    if (this.dec.length !== this.count) {
      const d = new Uint8Array(this.count);
      d.set(this.dec.subarray(0, Math.min(this.dec.length, d.length)));
      this.dec = d;
    }
  }

  async read(index: number, n: number): Promise<Uint8Array> {
    await this.flush();
    const all = this.parts[0] ?? new Uint8Array(0);
    return all.subarray(index * RECORD_BYTES, Math.min(all.length, (index + n) * RECORD_BYTES));
  }

  decisions(): Uint8Array {
    if (this.dec.length !== this.count) {
      const d = new Uint8Array(this.count);
      d.set(this.dec.subarray(0, Math.min(this.dec.length, d.length)));
      this.dec = d;
    }
    return this.dec;
  }

  async saveDecisions(): Promise<void> {
    /* memory only */
  }

  async reset(): Promise<void> {
    this.parts = [];
    this.total = 0;
    this.dec = new Uint8Array(0);
  }
}

export interface StreamOptions {
  format_hint?: "auto" | "csv" | "txt" | "edifact" | "x12" | "xml";
  columns?: (string | number)[];
  has_header?: boolean;
  delimiter?: string;
  owner_policy?: "strict" | "lenient";
  trust?: boolean;
  chunk_bytes?: number;
}

export interface Counts {
  bytes_total: number;
  bytes_done: number;
  records: number;
  identifiers: number;
  valid: number;
  corrected: number;
  flagged: number;
  invalid_structure: number;
  missing: number;
  not_a_target: number;
  stored: number;
}

export interface StreamResult {
  format: string;
  detection: string;
  encoding: Codec;
  delimiter: string | null;
  counts: Counts;
  cancelled: boolean;
  elapsed_ms: number;
  parser_version: string;
}

export interface StreamProgress {
  counts: Counts;
  phase: string;
}

interface Item {
  token: string;
  raw: string;
  offset: number;
  x12?: [string, string, string | null];
}

function detect(head: string, opts: StreamOptions): { format: string; reason: string; delimiter: string | null; separators?: Sep; delims?: X12Delims } {
  const stripped = head.replace(/^[\uFEFF \r\n\t]+/, "");
  let fmt = opts.format_hint && opts.format_hint !== "auto" ? opts.format_hint : "";
  if (!fmt) {
    if (/^UN[ABH]/.test(stripped)) fmt = "edifact";
    else if (/^(ISA|GS|ST)\*/.test(stripped)) fmt = "x12";
    else if (stripped.startsWith("<")) fmt = "xml";
    else if (opts.columns && opts.columns.length) fmt = "csv";
    else {
      const first = stripped.split("\n", 1)[0] ?? "";
      fmt = /[,;\t]/.test(first) ? "csv" : "txt";
    }
  }
  const out: ReturnType<typeof detect> = { format: fmt, reason: "content", delimiter: null };
  if (fmt === "csv") {
    const first = (head.replace(/^\uFEFF/, "").split("\n", 1)[0] ?? "");
    let delim = opts.delimiter ?? null;
    if (!delim) {
      const counts = [",", ";", "\t", "|"].map((d) => [d, first.split(d).length - 1] as [string, number]);
      counts.sort((a, b) => b[1] - a[1]);
      delim = counts[0] && counts[0][1] > 0 ? counts[0][0] : ",";
    }
    out.delimiter = delim;
  }
  if (fmt === "edifact") {
    const sep: Sep = { component: ":", element: "+", release: "?", segment: "'" };
    if (stripped.startsWith("UNA") && stripped.length >= 9) {
      sep.component = stripped[3] as string;
      sep.element = stripped[4] as string;
      sep.release = stripped[6] as string;
      sep.segment = stripped[8] as string;
    }
    out.separators = sep;
  }
  if (fmt === "x12") {
    const d: X12Delims = { element: "*", segment: "~" };
    if (stripped.startsWith("ISA")) {
      d.element = stripped[3] ?? "*";
      if (stripped.length >= 106) {
        const seg = stripped[105] as string;
        if (!/[A-Za-z0-9 ]/.test(seg) && seg !== d.element) d.segment = seg;
        else d.segment = stripped.slice(0, 200).includes("~") ? "~" : "\n";
      }
    }
    out.delims = d;
  }
  return out;
}

interface Sep { component: string; element: string; release: string; segment: string }
interface X12Delims { element: string; segment: string }

const RE_TOKEN = /(?<![A-Z0-9])[A-Z]{4} ?[0-9]{6} ?[0-9](?![A-Z0-9])/g;
const XML_EQID_ATTRS = new Set(["container/eqid", "equipment/eqid", "unit/id", "unit/unique-key", "line-discharge-list/unit-id"]);
const RE_XML_NAME = /[A-Za-z_][\w.:-]*/y;
const RE_XML_ATTR = /\s([A-Za-z_][\w.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

function splitOn(s: string, delim: string, release: string): string[] {
  const out: string[] = [];
  let buf = "";
  let escaped = false;
  for (const ch of s) {
    if (escaped) { buf += ch; escaped = false; continue; }
    if (release && ch === release) { escaped = true; continue; }
    if (ch === delim) { out.push(buf); buf = ""; continue; }
    buf += ch;
  }
  out.push(buf);
  return out;
}

async function* itemsOf(chunks: AsyncGenerator<string>, det: ReturnType<typeof detect>, opts: StreamOptions): AsyncGenerator<[number, Item[]]> {
  if (det.format === "csv") {
    const columns = opts.columns ?? [];
    if (!columns.length) throw new Error("csv needs a column mapping naming the identifier column(s)");
    const hasHeader = opts.has_header ?? true;
    let indices: number[] | null = null;
    for await (const rec of csvRecords(chunks, det.delimiter ?? ",")) {
      if (indices === null) {
        const header = new Map<string, number>();
        if (hasHeader) rec.fields.forEach((f, i) => header.set(f.value.trim().replace(/^\uFEFF/, "").toLowerCase(), i));
        indices = columns.map((c) => {
          if (typeof c === "number" || (/^\d+$/.test(String(c)) && !hasHeader)) return Number(c);
          const idx = header.get(String(c).trim().toLowerCase());
          if (idx === undefined) throw new Error(`column ${JSON.stringify(c)} not found in header row (have: ${[...header.keys()].sort().join(", ")})`);
          return idx;
        });
        if (hasHeader) continue;
      }
      if (rec.fields.length === 1 && rec.fields[0]!.value === "") continue;
      const items: Item[] = [];
      for (const idx of indices) {
        const f = rec.fields[idx];
        if (!f) { items.push({ token: "", raw: "", offset: rec.end }); continue; }
        let offset = f.start + (f.quoted ? 1 : 0);
        const raw = f.value.trim();
        if (raw === "" || (f.quoted && raw.includes('"'))) { items.push({ token: "", raw: "", offset }); continue; }
        offset += f.value.length - f.value.trimStart().length;
        items.push({ token: raw, raw, offset });
      }
      yield [rec.no, items];
    }
    return;
  }
  if (det.format === "txt") {
    for await (const [lineNo, start, line] of lineRecords(chunks)) {
      const upper = line.toUpperCase();
      const items: Item[] = [];
      for (const m of upper.matchAll(RE_TOKEN)) {
        const idx = m.index ?? 0;
        if (line.slice(idx, idx + m[0].length) === m[0]) items.push({ token: m[0], raw: m[0], offset: start + idx });
      }
      yield [lineNo - 1, items];
    }
    return;
  }
  if (det.format === "edifact") {
    const sep = det.separators!;
    let segNo = 0;
    for await (const [start, raw] of delimitedSegments(chunks, sep.segment, sep.release, true)) {
      segNo += 1;
      if (!raw.startsWith("EQD")) continue;
      const els = splitOn(raw, sep.element, sep.release).map((e) => splitOn(e, sep.component, sep.release));
      const qualifier = els[1]?.[0] ?? "";
      if (qualifier !== "CN") continue;
      const eqid = els[2]?.[0] ?? "";
      if (!eqid) { yield [segNo, [{ token: "", raw: "", offset: start }]]; continue; }
      yield [segNo, [{ token: eqid, raw: eqid, offset: start + raw.indexOf(eqid) }]];
    }
    return;
  }
  if (det.format === "x12") {
    const d = det.delims!;
    let segNo = 0;
    for await (const [start, raw] of delimitedSegments(chunks, d.segment, "", false)) {
      segNo += 1;
      const els = raw.split(d.element);
      const tag = els[0];
      if (tag === "N7") {
        const initial = els[1] ?? "";
        const number = els[2] ?? "";
        const has18 = els.length > 18;
        if (!initial && !number) { yield [segNo, [{ token: "", raw: "", offset: start }]]; continue; }
        const printed = has18 ? (els[18] as string) : null;
        let offset = start + raw.length;
        if (has18) offset = start + els.slice(0, 18).reduce((n, e) => n + e.length + 1, 0);
        const token = has18 ? `${initial}*${number}*${printed}` : `${initial}*${number}`;
        yield [segNo, [{ token, raw: printed ?? "", offset, x12: [initial, number, printed] }]];
      } else if (tag === "N9" && els.length > 2 && els[1] === "EQ") {
        const token = els[2] as string;
        const offset = start + (els[0] as string).length + 1 + 3;
        yield [segNo, [token ? { token, raw: token, offset } : { token: "", raw: "", offset }]];
      }
    }
    return;
  }
  if (det.format === "xml") {
    let record = 0;
    for await (const [tagStart, tag] of xmlStartTags(chunks)) {
      RE_XML_NAME.lastIndex = 1;
      const m = RE_XML_NAME.exec(tag);
      if (!m) continue;
      const element = m[0].split(":").pop() as string;
      if (!["container", "equipment", "unit", "line-discharge-list"].includes(element)) continue;
      const items: Item[] = [];
      RE_XML_ATTR.lastIndex = m.index + m[0].length;
      let am: RegExpExecArray | null;
      while ((am = RE_XML_ATTR.exec(tag)) !== null) {
        const attr = (am[1] as string).split(":").pop() as string;
        if (!XML_EQID_ATTRS.has(`${element}/${attr}`)) continue;
        const value = am[2] !== undefined ? am[2] : (am[3] as string);
        const valueOffset = tagStart + am.index + am[0].indexOf(am[2] !== undefined ? `"${am[2]}"` : `'${am[3]}'`) + 1;
        items.push(value ? { token: value, raw: value, offset: valueOffset } : { token: "", raw: "", offset: valueOffset });
      }
      if (!items.length) continue;
      record += 1;
      yield [record - 1, items];
    }
    return;
  }
  throw new Error(`format ${det.format} is not on the streaming path`);
}

function evaluate(item: Item, context: FieldContext, ownerPolicy: "strict" | "lenient"): { res: CorrectionResult | null; candidate: string | null } {
  if (item.x12) {
    const [initial, number, printed] = item.x12;
    if (printed === null) return { res: null, candidate: null };
    const res = correctX12Equipment(initial, number, printed || null, {});
    return { res, candidate: res.status === Status.CORRECTED ? res.computed_check : null };
  }
  const res = correctIdentifier(item.token, context, { owner_policy: ownerPolicy });
  return { res, candidate: res.status === Status.CORRECTED ? res.corrected : null };
}

export interface StreamRunOptions extends StreamOptions {
  signal?: { cancelled: boolean };
  onProgress?: (p: StreamProgress) => void;
  progress_every_bytes?: number;
}

/** Stream the source once into the store. Restarts as latin-1 when UTF-8 fails. */
export async function runStream(source: ByteSource, store: ProposalStore, opts: StreamRunOptions = {}): Promise<StreamResult> {
  if (source.size > MAX_LARGE_BYTES) throw new Error(`file is ${source.size} bytes; the streaming inspector handles up to ${MAX_LARGE_BYTES}`);
  const started = Date.now();
  let encoding: Codec = "utf-8";
  for (let attempt = 0; attempt < 2; attempt++) {
    await store.reset();
    try {
      const r = await runPass(source, store, opts, encoding, started);
      return r;
    } catch (err) {
      if (err instanceof CodecError && encoding === "utf-8") {
        encoding = "latin-1";
        continue;
      }
      throw err;
    }
  }
  throw new Error("unreachable");
}

async function runPass(source: ByteSource, store: ProposalStore, opts: StreamRunOptions, encoding: Codec, started: number): Promise<StreamResult> {
  const counts: Counts = { bytes_total: source.size, bytes_done: 0, records: 0, identifiers: 0, valid: 0, corrected: 0, flagged: 0,
    invalid_structure: 0, missing: 0, not_a_target: 0, stored: 0 };
  const headBytes = await source.slice(0, Math.min(source.size, 256 * 1024));
  let head: string;
  try {
    head = new TextDecoder(encoding === "utf-8" ? "utf-8" : "windows-1252", { fatal: false, ignoreBOM: true }).decode(headBytes);
  } catch {
    head = "";
  }
  const det = detect(head, opts);
  const context = opts.trust || det.format !== "txt" ? FieldContext.EQUIPMENT_ID : FieldContext.FREE_TEXT;
  const ownerPolicy = opts.owner_policy ?? "strict";
  const every = opts.progress_every_bytes ?? 8 * 1024 * 1024;
  let lastReport = 0;
  let cancelled = false;
  const buffer = new Uint8Array(RECORD_BYTES * 4096);
  const view = new DataView(buffer.buffer);
  let fill = 0;
  const chunks = textChunks(source, encoding, opts.chunk_bytes, (done) => {
    counts.bytes_done = done;
  });
  const flushBuf = async () => {
    if (fill) {
      await store.append(buffer.subarray(0, fill));
      fill = 0;
    }
  };
  const push = (rec: Omit<StoredRecord, "index" | "location">) => {
    encodeRecord(view, fill, rec);
    fill += RECORD_BYTES;
    counts.stored += 1;
  };
  for await (const [recordNo, items] of itemsOf(chunks, det, opts)) {
    counts.records += 1;
    for (const item of items) {
      if (item.token === "" && !item.x12) {
        counts.missing += 1;
        push({ offset: item.offset, record_no: recordNo, kind: "missing", scheme: "unknown", raw: "", raw_truncated: false, candidate: null, x12_slot: false });
        continue;
      }
      counts.identifiers += 1;
      const { res, candidate } = evaluate(item, context, ownerPolicy);
      if (res === null) {
        counts.flagged += 1;
        push({ offset: item.offset, record_no: recordNo, kind: "flagged", scheme: "iso6346", raw: item.token, raw_truncated: false, candidate: null, x12_slot: true });
        continue;
      }
      const status = res.status;
      if (status === Status.VALID) counts.valid += 1;
      else if (status === Status.CORRECTED) {
        counts.corrected += 1;
        push({ offset: item.offset, record_no: recordNo, kind: "corrected", scheme: res.id_type, raw: item.raw, raw_truncated: false, candidate, x12_slot: !!item.x12 });
      } else if (status === Status.FLAGGED) {
        counts.flagged += 1;
        push({ offset: item.offset, record_no: recordNo, kind: "flagged", scheme: res.id_type, raw: item.raw, raw_truncated: false, candidate: null, x12_slot: !!item.x12 });
      } else if (status === Status.INVALID_STRUCTURE) {
        counts.invalid_structure += 1;
        push({ offset: item.offset, record_no: recordNo, kind: "invalid_structure", scheme: res.id_type, raw: item.raw, raw_truncated: false, candidate: null, x12_slot: !!item.x12 });
      } else counts.not_a_target += 1;
    }
    if (fill >= buffer.length - RECORD_BYTES * 8) await flushBuf();
    if (counts.bytes_done - lastReport >= every) {
      lastReport = counts.bytes_done;
      opts.onProgress?.({ counts: { ...counts }, phase: "streaming" });
      if (opts.signal?.cancelled) {
        cancelled = true;
        break;
      }
    }
  }
  await flushBuf();
  await store.flush();
  if (!cancelled) counts.bytes_done = source.size;
  opts.onProgress?.({ counts: { ...counts }, phase: cancelled ? "cancelled" : "done" });
  return { format: det.format, detection: det.reason, encoding, delimiter: det.delimiter, counts, cancelled, elapsed_ms: Date.now() - started,
    parser_version: LARGE_PARSER_VERSION };
}

// --------------------------------------------------------------------------- //
// Review
// --------------------------------------------------------------------------- //

export interface Filter {
  kind?: Kind | "all";
  decision?: Decision | "all";
  /** Restrict to one stored record (row-level decisions). */
  index?: number;
}

function matches(filter: Filter, kind: Kind, decision: Decision): boolean {
  if (filter.kind && filter.kind !== "all" && filter.kind !== kind) return false;
  if (filter.decision && filter.decision !== "all" && filter.decision !== decision) return false;
  return true;
}

/** Visit stored records matching the filter in store order; return false from visit to stop. */
async function scanRecords(store: ProposalStore, filter: Filter, from: number,
  visit: (view: DataView, at: number, index: number, kind: Kind, decisionCode: number) => boolean | void): Promise<void> {
  const dec = store.decisions();
  const total = store.count;
  let start = Math.max(0, from);
  let end = total;
  if (filter.index !== undefined) {
    if (filter.index < start || filter.index >= total) return;
    start = filter.index;
    end = filter.index + 1;
  }
  for (let i = start; i < end; i += 4096) {
    const batch = Math.min(4096, end - i);
    const bytes = await store.read(i, batch);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let k = 0; k < batch; k++) {
      const kind = KIND_NAME[view.getUint8(k * RECORD_BYTES + 12)] ?? "corrected";
      const code = dec[i + k] ?? 0;
      if (!matches(filter, kind, DECISION_NAME[code] ?? "proposed")) continue;
      if (visit(view, k * RECORD_BYTES, i + k, kind, code) === false) return;
    }
  }
}

export type PageItem = StoredRecord & { decision: Decision };

export async function readPage(store: ProposalStore, from: number, n: number, filter: Filter = {}): Promise<{ items: PageItem[]; next: number | null }> {
  const items: PageItem[] = [];
  let next: number | null = null;
  await scanRecords(store, filter, from, (view, at, index, _kind, code) => {
    if (items.length === n) {
      next = index;
      return false;
    }
    items.push({ ...decodeRecord(view, at, index), decision: DECISION_NAME[code] ?? "proposed" });
    return true;
  });
  return { items, next };
}

/** Count records matching a filter; proposals = corrected rows in the decidable population. */
export async function countMatching(store: ProposalStore, filter: Filter, population: readonly Decision[] = ["proposed", "deferred"]): Promise<{ count: number; proposals: number }> {
  const popCodes = new Set(population.map((p) => DECISION_CODE[p]));
  let count = 0;
  let proposals = 0;
  await scanRecords(store, filter, 0, (_view, _at, _index, kind, code) => {
    count += 1;
    if (kind === "corrected" && popCodes.has(code)) proposals += 1;
  });
  return { count, proposals };
}

export interface ApprovalRecord {
  id: string;
  decision: Decision;
  count: number;
  filter: Filter;
  digest: string;
  decided_at: string;
}

/**
 * Decide every corrected record matching the filter that is still in the
 * population. `expected` is the count the reviewer saw; a different count
 * means the selection drifted and nothing is written.
 */
export async function decideMatching(store: ProposalStore, filter: Filter, decision: Exclude<Decision, "proposed">, expected: number | null,
  population: readonly Decision[] = ["proposed", "deferred"]): Promise<ApprovalRecord> {
  const { proposals } = await countMatching(store, filter, population);
  if (expected !== null && expected !== proposals) throw new Error(`SELECTION_DRIFT: filter matches ${proposals} proposals, expected ${expected}`);
  if (proposals === 0) throw new Error("EMPTY_SELECTION: the filter matches no undecided proposals");
  const dec = store.decisions();
  const popCodes = new Set(population.map((p) => DECISION_CODE[p]));
  const changed: number[] = [];
  await scanRecords(store, filter, 0, (_view, _at, index, kind, code) => {
    if (kind !== "corrected" || !popCodes.has(code)) return;
    dec[index] = DECISION_CODE[decision];
    changed.push(index);
  });
  await store.saveDecisions();
  const digest = await digestIndices(changed);
  return { id: `apr_${Date.now().toString(16)}_${digest.slice(0, 8)}`, decision, count: changed.length, filter, digest, decided_at: new Date().toISOString() };
}

/** Return decided records to the proposed state (undo). */
export async function undoMatching(store: ProposalStore, filter: Filter): Promise<number> {
  const dec = store.decisions();
  let n = 0;
  await scanRecords(store, filter, 0, (_view, _at, index, _kind, code) => {
    if (code === DECISION_CODE.proposed) return;
    dec[index] = DECISION_CODE.proposed;
    n += 1;
  });
  await store.saveDecisions();
  return n;
}

export type Summary = Record<Kind, Record<Decision, number>>;

/** Counts by kind and decision in one pass over the store. */
export async function summarize(store: ProposalStore): Promise<Summary> {
  const out = {} as Summary;
  for (const k of ["corrected", "flagged", "invalid_structure", "missing"] as Kind[]) {
    out[k] = { proposed: 0, approved: 0, rejected: 0, deferred: 0 };
  }
  await scanRecords(store, {}, 0, (_view, _at, _index, kind, code) => {
    out[kind][DECISION_NAME[code] ?? "proposed"] += 1;
  });
  return out;
}

async function digestIndices(indices: number[]): Promise<string> {
  const buf = new Uint32Array(indices);
  const digest = await crypto.subtle.digest("SHA-256", buf.buffer as ArrayBuffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function decisionsDigest(store: ProposalStore): Promise<string> {
  const dec = store.decisions();
  const digest = await crypto.subtle.digest("SHA-256", dec.buffer.slice(dec.byteOffset, dec.byteOffset + dec.byteLength) as ArrayBuffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --------------------------------------------------------------------------- //
// Export
// --------------------------------------------------------------------------- //

export interface Sink {
  write(bytes: Uint8Array): Promise<void>;
  close(): Promise<void>;
}

export class MemorySink implements Sink {
  parts: Uint8Array[] = [];
  async write(bytes: Uint8Array): Promise<void> {
    this.parts.push(bytes.slice());
  }
  async close(): Promise<void> {}
  bytes(): Uint8Array<ArrayBuffer> {
    const total = this.parts.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(new ArrayBuffer(total));
    let at = 0;
    for (const p of this.parts) {
      out.set(p, at);
      at += p.length;
    }
    return out;
  }
}

/**
 * Text writer over a byte sink. Pieces are gathered into ~1 MiB buffers before
 * encoding, so a splice that writes hundreds of thousands of short spans still
 * reaches the file system as large writes.
 */
export class TextSink {
  private parts: string[] = [];
  private length = 0;
  private readonly encoder = new TextEncoder();
  constructor(private readonly sink: Sink, private readonly encoding: Codec) {}

  async write(text: string): Promise<void> {
    if (!text) return;
    this.parts.push(text);
    this.length += text.length;
    if (this.length >= 1024 * 1024) await this.flush();
  }

  async flush(): Promise<void> {
    if (!this.parts.length) return;
    const text = this.parts.length === 1 ? (this.parts[0] as string) : this.parts.join("");
    this.parts = [];
    this.length = 0;
    if (this.encoding === "utf-8") return this.sink.write(this.encoder.encode(text));
    const out = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      if (code > 0xff) throw new Error(`character U+${code.toString(16).toUpperCase()} cannot be encoded as latin-1`);
      out[i] = code;
    }
    return this.sink.write(out);
  }

  async close(): Promise<void> {
    await this.flush();
    await this.sink.close();
  }
}

export interface ExportResult {
  edits_applied: number;
  bytes_written: number;
  sha256: string;
  exceptions: number;
}

/** Approved corrected records in offset order (the store is already in file order). */
async function* approvedEdits(store: ProposalStore): AsyncGenerator<StoredRecord> {
  const dec = store.decisions();
  const total = store.count;
  for (let i = 0; i < total; i += 4096) {
    const batch = Math.min(4096, total - i);
    const bytes = await store.read(i, batch);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let k = 0; k < batch; k++) {
      if ((dec[i + k] ?? 0) !== DECISION_CODE.approved) continue;
      if (view.getUint8(k * RECORD_BYTES + 12) !== KIND_CODE.corrected) continue;
      yield decodeRecord(view, k * RECORD_BYTES, i + k);
    }
  }
}

export async function exportCorrected(source: ByteSource, encoding: Codec, store: ProposalStore, sink: Sink, opts: { chunk_bytes?: number;
  onProgress?: (done: number, total: number) => void; signal?: { cancelled: boolean } } = {}): Promise<ExportResult> {
  const edits = approvedEdits(store);
  let cur = await edits.next();
  let edit = cur.done ? null : cur.value;
  const hasher = await streamingSha256();
  const counting: Sink = {
    write: async (b) => { hasher.update(b); written += b.length; await sink.write(b); },
    close: () => sink.close(),
  };
  let written = 0;
  const out = new TextSink(counting, encoding);
  let carry = "";
  let pos = 0;
  let applied = 0;
  for await (const chunk of textChunks(source, encoding, opts.chunk_bytes, (done) => opts.onProgress?.(done, source.size))) {
    if (opts.signal?.cancelled) throw new Error("EXPORT_CANCELLED");
    const text = carry + chunk;
    const base = pos - carry.length;
    carry = "";
    let cursor = 0;
    while (edit !== null && edit.offset < base + text.length) {
      const rel = edit.offset - base;
      if (rel < cursor) throw new Error(`EDIT_OVERLAP: record ${edit.index} overlaps a previous edit`);
      if (rel + edit.raw.length > text.length) break;
      if (text.slice(rel, rel + edit.raw.length) !== edit.raw) throw new Error(`EDIT_MISMATCH: record ${edit.index} at offset ${edit.offset}: source text differs`);
      await out.write(text.slice(cursor, rel));
      await out.write(edit.candidate ?? "");
      cursor = rel + edit.raw.length;
      applied += 1;
      cur = await edits.next();
      edit = cur.done ? null : cur.value;
    }
    if (edit !== null && edit.offset < base + text.length) {
      const rel = edit.offset - base;
      await out.write(text.slice(cursor, rel));
      carry = text.slice(rel);
    } else {
      await out.write(text.slice(cursor));
    }
    pos = base + text.length;
  }
  if (carry) await out.write(carry);
  if (edit !== null) throw new Error(`EDIT_BEYOND_END: record ${edit.index} offset ${edit.offset} past end`);
  await out.close();
  return { edits_applied: applied, bytes_written: written, sha256: await hasher.digest(), exceptions: store.count - applied };
}

/** Exceptions CSV (every stored record that is not an approved correction) written through a text sink. */
export async function writeExceptions(store: ProposalStore, sink: Sink): Promise<number> {
  const out = new TextSink(sink, "utf-8");
  await out.write("index,location,raw,kind,decision,candidate\r\n");
  const dec = store.decisions();
  let n = 0;
  const total = store.count;
  for (let i = 0; i < total; i += 4096) {
    const batch = Math.min(4096, total - i);
    const bytes = await store.read(i, batch);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let lines = "";
    for (let k = 0; k < batch; k++) {
      const r = decodeRecord(view, k * RECORD_BYTES, i + k);
      const d = DECISION_NAME[dec[i + k] ?? 0] ?? "proposed";
      if (r.kind === "corrected" && d === "approved") continue;
      lines += `${r.index},${csvCell(r.location)},${csvCell(r.raw)},${r.kind},${d},${csvCell(r.candidate ?? "")}\r\n`;
      n += 1;
    }
    await out.write(lines);
  }
  await out.close();
  return n;
}

export async function writeLedger(store: ProposalStore, sink: Sink): Promise<number> {
  const out = new TextSink(sink, "utf-8");
  await out.write("index,location,char_offset,before,after\r\n");
  let n = 0;
  let lines = "";
  for await (const r of approvedEdits(store)) {
    lines += `${r.index},${csvCell(r.location)},${r.offset},${csvCell(r.raw)},${csvCell(r.candidate ?? "")}\r\n`;
    n += 1;
    if (lines.length > 65536) {
      await out.write(lines);
      lines = "";
    }
  }
  await out.write(lines);
  await out.close();
  return n;
}

function csvCell(v: string): string {
  return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/**
 * Digest of a byte stream written in arbitrary pieces. SubtleCrypto has no
 * incremental interface, so fixed 1 MiB blocks are hashed as they fill and the
 * block digests plus the total length are hashed once at the end. The value is
 * stable for the same bytes regardless of write boundaries and is labelled
 * "blocks-" so it is never mistaken for a plain SHA-256 of the file.
 */
async function streamingSha256(): Promise<{ update(b: Uint8Array): void; digest(): Promise<string> }> {
  const block = new Uint8Array(1024 * 1024);
  let fill = 0;
  const blockDigests: Uint8Array[] = [];
  let totalBytes = 0;
  const pending: Promise<void>[] = [];
  // The block is copied and released synchronously so update() can keep
  // filling while the digest of the previous block is still computing.
  const flush = () => {
    const copy = block.slice(0, fill);
    const slot = blockDigests.length;
    blockDigests.push(new Uint8Array(0));
    fill = 0;
    pending.push(
      crypto.subtle.digest("SHA-256", copy as BufferSource).then((d) => {
        blockDigests[slot] = new Uint8Array(d);
      }),
    );
  };
  return {
    update(b: Uint8Array) {
      totalBytes += b.length;
      let at = 0;
      while (at < b.length) {
        const take = Math.min(block.length - fill, b.length - at);
        block.set(b.subarray(at, at + take), fill);
        fill += take;
        at += take;
        if (fill === block.length) flush();
      }
    },
    async digest() {
      if (fill || blockDigests.length === 0) flush();
      await Promise.all(pending);
      const all = new Uint8Array(blockDigests.length * 32 + 8);
      blockDigests.forEach((d, i) => all.set(d, i * 32));
      new DataView(all.buffer).setFloat64(blockDigests.length * 32, totalBytes);
      const d = new Uint8Array(await crypto.subtle.digest("SHA-256", all as BufferSource));
      return "blocks-" + [...d].map((x) => x.toString(16).padStart(2, "0")).join("");
    },
  };
}
