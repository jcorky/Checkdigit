/*
 * Chunked readers for large files, the browser counterpart of
 * checkdigit/workspace/stream.py. A file is read in fixed slices and decoded
 * incrementally, so a multibyte sequence split across a slice boundary is never
 * corrupted; records are yielded with the absolute character offset (UTF-16
 * units) of their raw span, so a later pass can splice edits while streaming
 * the same file again.
 *
 * Readers:
 *   textChunks        bytes -> decoded text chunks with a chosen codec
 *   csvRecords        RFC 4180 records across quoted newlines and chunk boundaries
 *   lineRecords       one record per physical line
 *   delimitedSegments terminator-delimited segments honouring a release character
 *   xmlStartTags      start tags across chunk boundaries, skipping comments and CDATA
 *
 * The codec is strict UTF-8 unless a byte sequence fails to decode, in which
 * case the caller restarts the whole pass as ISO-8859-1 (the same rule as the
 * service). Latin-1 is decoded by hand: the browser's "latin1" label means
 * windows-1252, which would change bytes 0x80-0x9F.
 */

import type { Codec } from "./encoding";

export const CHUNK_BYTES = 4 * 1024 * 1024;

export class CodecError extends Error {
  constructor(public readonly byteOffset: number) {
    super(`not valid UTF-8 near byte ${byteOffset}`);
  }
}

export interface ByteSource {
  size: number;
  slice(start: number, end: number): Promise<Uint8Array>;
}

export function blobSource(blob: Blob): ByteSource {
  return {
    size: blob.size,
    slice: async (start, end) => new Uint8Array(await blob.slice(start, end).arrayBuffer()),
  };
}

export function bytesSource(bytes: Uint8Array): ByteSource {
  return { size: bytes.length, slice: async (start, end) => bytes.subarray(start, end) };
}

function latin1(bytes: Uint8Array): string {
  let text = "";
  const step = 8192;
  for (let i = 0; i < bytes.length; i += step) {
    text += String.fromCharCode(...bytes.subarray(i, Math.min(bytes.length, i + step)));
  }
  return text;
}

/** Decoded text chunks. Throws CodecError on invalid UTF-8 when codec is "utf-8". */
export async function* textChunks(source: ByteSource, codec: Codec = "utf-8", chunkBytes = CHUNK_BYTES,
  onBytes?: (done: number) => void): AsyncGenerator<string> {
  const decoder = codec === "utf-8" ? new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }) : null;
  let offset = 0;
  while (offset < source.size) {
    const end = Math.min(source.size, offset + chunkBytes);
    const bytes = await source.slice(offset, end);
    let text: string;
    if (decoder) {
      try {
        text = decoder.decode(bytes, { stream: end < source.size });
      } catch {
        throw new CodecError(offset);
      }
    } else {
      text = latin1(bytes);
    }
    offset = end;
    onBytes?.(offset);
    if (text) yield text;
  }
}

export interface Field {
  col: number;
  value: string;
  start: number;
  end: number;
  quoted: boolean;
}

export interface Record {
  no: number;
  start: number;
  end: number;
  fields: Field[];
}

/** RFC 4180 state machine over a stream of text chunks; state survives chunk boundaries. */
export async function* csvRecords(chunks: AsyncIterable<string> | Iterable<string>, delimiter: string): AsyncGenerator<Record> {
  const special = new RegExp("[" + delimiter.replace(/[\\\]^-]/g, "\\$&") + '"\r\n]', "g");
  let row: Field[] = [];
  let buf: string[] = [];
  let inQuotes = false;
  let afterQuote = false;
  let quotedField = false;
  let fieldStart = 0;
  let recordStart = 0;
  let pos = 0;
  let recNo = 0;
  let pendingCr = false;
  let sawAny = false;

  for await (const chunk of chunks as AsyncIterable<string>) {
    let i = 0;
    const n = chunk.length;
    if (n) sawAny = true;
    if (pendingCr) {
      pendingCr = false;
      if (n && chunk[0] === "\n") {
        i = 1;
        pos += 1;
        fieldStart = pos;
        recordStart = pos;
      }
    }
    while (i < n) {
      if (inQuotes) {
        if (afterQuote) {
          afterQuote = false;
          if (chunk[i] === '"') {
            buf.push('"');
            i += 1;
            pos += 1;
            continue;
          }
          inQuotes = false;
        } else {
          const j = chunk.indexOf('"', i);
          if (j < 0) {
            buf.push(chunk.slice(i));
            pos += n - i;
            i = n;
            continue;
          }
          buf.push(chunk.slice(i, j));
          pos += j - i + 1;
          i = j + 1;
          afterQuote = true;
          continue;
        }
      }
      const ch = chunk[i] as string;
      if (ch === '"' && pos === fieldStart) {
        inQuotes = true;
        quotedField = true;
        i += 1;
        pos += 1;
        continue;
      }
      if (ch === delimiter) {
        row.push({ col: row.length, value: buf.join(""), start: fieldStart, end: pos, quoted: quotedField });
        buf = [];
        quotedField = false;
        i += 1;
        pos += 1;
        fieldStart = pos;
        continue;
      }
      if (ch === "\r" || ch === "\n") {
        row.push({ col: row.length, value: buf.join(""), start: fieldStart, end: pos, quoted: quotedField });
        buf = [];
        quotedField = false;
        const rec: Record = { no: recNo, start: recordStart, end: pos, fields: row };
        row = [];
        recNo += 1;
        let step = 1;
        if (ch === "\r") {
          if (i + 1 < n) step = chunk[i + 1] === "\n" ? 2 : 1;
          else pendingCr = true;
        }
        i += step;
        pos += step;
        fieldStart = pos;
        recordStart = pos;
        yield rec;
        continue;
      }
      special.lastIndex = i;
      const m = special.exec(chunk);
      const j = m ? m.index : n;
      buf.push(chunk.slice(i, j));
      pos += j - i;
      i = j;
    }
  }
  if (sawAny && (buf.length || row.length || quotedField || pos > recordStart)) {
    row.push({ col: row.length, value: buf.join(""), start: fieldStart, end: pos, quoted: quotedField });
    yield { no: recNo, start: recordStart, end: pos, fields: row };
  }
}

/** Yield [lineNo (1-based), absoluteStart, lineTextWithoutBreak]. */
export async function* lineRecords(chunks: AsyncIterable<string> | Iterable<string>): AsyncGenerator<[number, number, string]> {
  let carry = "";
  let pos = 0;
  let lineNo = 0;
  let pendingCr = false;
  for await (const chunk of chunks as AsyncIterable<string>) {
    const text = carry + chunk;
    carry = "";
    let start = 0;
    const n = text.length;
    if (pendingCr) {
      pendingCr = false;
      if (n && text[0] === "\n") start = 1;
    }
    let i = start;
    while (i < n) {
      const cr = text.indexOf("\r", i);
      const lf = text.indexOf("\n", i);
      let brk = -1;
      if (cr >= 0 && lf >= 0) brk = Math.min(cr, lf);
      else brk = cr >= 0 ? cr : lf;
      if (brk < 0) break;
      lineNo += 1;
      yield [lineNo, pos + start, text.slice(start, brk)];
      if (text[brk] === "\r") {
        if (brk + 1 < n) i = brk + (text[brk + 1] === "\n" ? 2 : 1);
        else {
          pendingCr = true;
          i = brk + 1;
        }
      } else {
        i = brk + 1;
      }
      start = i;
    }
    carry = text.slice(start);
    pos += start;
  }
  if (carry) {
    lineNo += 1;
    yield [lineNo, pos, carry];
  }
}

/**
 * Terminator-delimited segments (EDIFACT, X12). `release` is the escape character
 * ("?" for EDIFACT; "" when the syntax has none). The raw segment keeps release
 * characters so it can be spliced back verbatim. With `skipUna`, a nine-character
 * UNA service string advice is skipped even when it spans chunks.
 */
export async function* delimitedSegments(chunks: AsyncIterable<string> | Iterable<string>, terminator: string, release = "",
  skipUna = false): AsyncGenerator<[number, string]> {
  const iter = (chunks as AsyncIterable<string>)[Symbol.asyncIterator]
    ? (chunks as AsyncIterable<string>)[Symbol.asyncIterator]()
    : (async function* () { yield* chunks as Iterable<string>; })();
  let head = "";
  let pos = 0;
  let start = 0;
  if (skipUna) {
    while (head.length < 9 && head.startsWith("UNA".slice(0, head.length))) {
      const r = await iter.next();
      if (r.done) break;
      head += r.value;
      if (!head.startsWith("UNA".slice(0, Math.min(3, head.length)))) break;
    }
    if (head.startsWith("UNA") && head.length >= 9) {
      pos = start = 9;
      head = head.slice(9);
    }
  }
  const buf: string[] = [];
  let escaped = false;
  let skippingWs = true;
  const specialSrc = "[" + [terminator, release].filter(Boolean).map((c) => c.replace(/[\\\]^-]/g, "\\$&")).join("") + "]";
  const special = new RegExp(specialSrc, "g");

  async function* withHead(): AsyncGenerator<string> {
    if (head) yield head;
    while (true) {
      const r = await iter.next();
      if (r.done) return;
      yield r.value;
    }
  }
  for await (const chunk of withHead()) {
    let i = 0;
    const n = chunk.length;
    while (i < n) {
      const ch = chunk[i] as string;
      if (skippingWs) {
        if (ch === "\r" || ch === "\n" || ch === " " || ch === "\t") {
          i += 1;
          pos += 1;
          start = pos;
          continue;
        }
        skippingWs = false;
      }
      if (escaped) {
        buf.push(ch);
        escaped = false;
        i += 1;
        pos += 1;
        continue;
      }
      if (release && ch === release) {
        buf.push(ch);
        escaped = true;
        i += 1;
        pos += 1;
        continue;
      }
      if (ch === terminator) {
        const seg = buf.join("");
        if (seg.trim()) yield [start, seg];
        buf.length = 0;
        skippingWs = true;
        i += 1;
        pos += 1;
        start = pos;
        continue;
      }
      special.lastIndex = i;
      const m = special.exec(chunk);
      const j = m ? m.index : n;
      buf.push(chunk.slice(i, j));
      pos += j - i;
      i = j;
    }
  }
  const tail = buf.join("");
  if (tail.trim()) yield [start, tail];
}

export class XmlSecurityError extends Error {}

const MAX_XML_CONSTRUCT = 4 * 1024 * 1024;

/**
 * Start tags (including self-closing ones) as [absoluteOffset, tagText]. Comments,
 * CDATA sections, processing instructions and end tags are skipped; quotes inside
 * attribute values are honoured; any construct may span chunks. DOCTYPE and
 * ENTITY declarations raise XmlSecurityError. Well-formedness is not verified.
 */
export async function* xmlStartTags(chunks: AsyncIterable<string> | Iterable<string>): AsyncGenerator<[number, string]> {
  let carry = "";
  let base = 0;
  for await (const chunk of chunks as AsyncIterable<string>) {
    const text = carry + chunk;
    let i = 0;
    const n = text.length;
    while (true) {
      const lt = text.indexOf("<", i);
      if (lt < 0) {
        i = n;
        break;
      }
      if (n - lt < 9) {
        i = lt;
        break;
      }
      if (text.startsWith("<!--", lt)) {
        const end = text.indexOf("-->", lt + 4);
        if (end < 0) { i = lt; break; }
        i = end + 3;
        continue;
      }
      if (text.startsWith("<![CDATA[", lt)) {
        const end = text.indexOf("]]>", lt + 9);
        if (end < 0) { i = lt; break; }
        i = end + 3;
        continue;
      }
      if (text.startsWith("<?", lt)) {
        const end = text.indexOf("?>", lt + 2);
        if (end < 0) { i = lt; break; }
        i = end + 2;
        continue;
      }
      if (text.startsWith("<!", lt)) throw new XmlSecurityError("DOCTYPE or ENTITY declaration refused");
      let j = lt + 1;
      let quote = "";
      let end = -1;
      while (j < n) {
        const ch = text[j] as string;
        if (quote) {
          if (ch === quote) quote = "";
        } else if (ch === '"' || ch === "'") {
          quote = ch;
        } else if (ch === ">") {
          end = j;
          break;
        }
        j += 1;
      }
      if (end < 0) { i = lt; break; }
      if (text[lt + 1] !== "/") yield [base + lt, text.slice(lt, end + 1)];
      i = end + 1;
    }
    carry = text.slice(i);
    base += i;
    if (carry.length > MAX_XML_CONSTRUCT) throw new Error("XML construct longer than the streaming limit");
  }
  if (carry.trimStart().startsWith("<!")) throw new XmlSecurityError("DOCTYPE or ENTITY declaration refused");
}

/** Helper for tests and small inputs: split a string into fixed-size chunks. */
export function* splitChunks(text: string, size: number): Generator<string> {
  for (let i = 0; i < text.length; i += size) yield text.slice(i, i + size);
}
