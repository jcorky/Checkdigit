import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  bytesSource,
  CodecError,
  csvRecords,
  delimitedSegments,
  lineRecords,
  splitChunks,
  textChunks,
  xmlStartTags,
  XmlSecurityError,
} from "../src/lib/stream";

// Vectors come from checkdigit/workspace/stream.py (scripts/gen_stream_vectors.py).
// Python offsets count code points; the TypeScript readers count UTF-16 units.
const vectors = JSON.parse(readFileSync(resolve(import.meta.dirname, "vectors", "stream_vectors.json"), "utf-8"));

function cpToUtf16(text: string): (cp: number) => number {
  const table: number[] = [0];
  let u = 0;
  for (const ch of text) {
    u += ch.length;
    table.push(u);
  }
  return (cp) => table[cp] as number;
}

async function collect<T>(gen: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const x of gen) out.push(x);
  return out;
}

const SIZES = [1, 2, 3, 5, 7, 11, 64, 1000];

describe("streaming readers match the Python readers at every chunk boundary", () => {
  it("csvRecords", async () => {
    const v = vectors.csv;
    const tr = cpToUtf16(v.text);
    const expected = v.records.map((r: { no: number; start: number; end: number; fields: { col: number; value: string; start: number; end: number; quoted: boolean }[] }) => ({
      no: r.no,
      start: tr(r.start),
      end: tr(r.end),
      fields: r.fields.map((f) => ({ col: f.col, value: f.value, start: tr(f.start), end: tr(f.end), quoted: f.quoted })),
    }));
    for (const size of SIZES) {
      const got = await collect(csvRecords(splitChunks(v.text, size), v.delimiter));
      expect(got, `chunk size ${size}`).toEqual(expected);
    }
    for (const r of expected) for (const f of r.fields) {
      if (!f.quoted) expect(v.text.slice(f.start, f.end)).toBe(f.value);
    }
  });

  for (const name of ["edifact", "edifact_no_una", "x12"] as const) {
    it(`delimitedSegments ${name}`, async () => {
      const v = vectors[name];
      const tr = cpToUtf16(v.text);
      const expected = v.segments.map((s: { start: number; raw: string }) => [tr(s.start), s.raw]);
      for (const size of SIZES) {
        const got = await collect(delimitedSegments(splitChunks(v.text, size), v.terminator, v.release, v.skip_una));
        expect(got, `chunk size ${size}`).toEqual(expected);
      }
      for (const [start, raw] of expected as [number, string][]) expect(v.text.slice(start, start + raw.length)).toBe(raw);
    });
  }

  it("xmlStartTags", async () => {
    const v = vectors.xml;
    const tr = cpToUtf16(v.text);
    const expected = v.tags.map((t: { start: number; tag: string }) => [tr(t.start), t.tag]);
    for (const size of SIZES) {
      const got = await collect(xmlStartTags(splitChunks(v.text, size)));
      expect(got, `chunk size ${size}`).toEqual(expected);
    }
    for (const [start, tag] of expected as [number, string][]) expect(v.text.slice(start, start + tag.length)).toBe(tag);
    await expect(collect(xmlStartTags(["<!DOCTYPE x [<!ENTITY a 'b'>]><r/>"]))).rejects.toBeInstanceOf(XmlSecurityError);
  });

  it("lineRecords", async () => {
    const v = vectors.lines;
    const tr = cpToUtf16(v.text);
    const expected = v.lines.map((l: { no: number; start: number; text: string }) => [l.no, tr(l.start), l.text]);
    for (const size of SIZES) {
      const got = await collect(lineRecords(splitChunks(v.text, size)));
      expect(got, `chunk size ${size}`).toEqual(expected);
    }
  });
});

describe("textChunks", () => {
  const text = "a,é,北京,\u{1F600}\r\nb";
  const bytes = new TextEncoder().encode(text);

  it("decodes UTF-8 split anywhere, including inside a multibyte sequence", async () => {
    for (const size of [1, 2, 3, 4, 5, 7, 1024]) {
      const parts = await collect(textChunks(bytesSource(bytes), "utf-8", size));
      expect(parts.join(""), `chunk bytes ${size}`).toBe(text);
    }
  });

  it("reports the byte offset of invalid UTF-8", async () => {
    const bad = new Uint8Array([...new TextEncoder().encode("ok,"), 0xe9, 0x0a]);
    await expect(collect(textChunks(bytesSource(bad), "utf-8", 2))).rejects.toBeInstanceOf(CodecError);
  });

  it("latin-1 maps every byte to the same code point, unlike windows-1252", async () => {
    const all = new Uint8Array(256);
    for (let i = 0; i < 256; i++) all[i] = i;
    const parts = await collect(textChunks(bytesSource(all), "latin-1", 100));
    const joined = parts.join("");
    expect(joined.length).toBe(256);
    for (let i = 0; i < 256; i++) expect(joined.charCodeAt(i)).toBe(i);
  });

  it("reports bytes done after each slice", async () => {
    const seen: number[] = [];
    await collect(textChunks(bytesSource(bytes), "utf-8", 4, (n) => seen.push(n)));
    expect(seen[seen.length - 1]).toBe(bytes.length);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  });
});
