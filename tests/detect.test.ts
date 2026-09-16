import { describe, expect, it } from "vitest";

import { detectBytes, detectFormat, zipMemberNames } from "../src/lib/dispatcher";
import { decodeBytes, encodeText } from "../src/lib/encoding";

// Minimal STORE-only ZIP writer for fixtures (local header + central directory + EOCD).
function zip(members: [string, Uint8Array][]): Uint8Array {
  const enc = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const u16 = (n: number) => [n & 0xff, (n >> 8) & 0xff];
  const u32 = (n: number) => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff];
  for (const [name, data] of members) {
    const nameBytes = enc.encode(name);
    const local = new Uint8Array([
      ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
      ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0), ...nameBytes,
    ]);
    chunks.push(local, data);
    central.push(new Uint8Array([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
      ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(0), ...u32(offset), ...nameBytes,
    ]));
    offset += local.length + data.length;
  }
  const cdStart = offset;
  const cd = central.reduce((n, c) => n + c.length, 0);
  const eocd = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(members.length), ...u16(members.length), ...u32(cd), ...u32(cdStart), ...u16(0)]);
  const total = [...chunks, ...central, eocd];
  const out = new Uint8Array(total.reduce((n, c) => n + c.length, 0));
  let p = 0;
  for (const c of total) {
    out.set(c, p);
    p += c.length;
  }
  return out;
}

const enc = (s: string) => new TextEncoder().encode(s);

describe("binary front door (dispatcher.detect_bytes)", () => {
  it("recognizes an Excel workbook", () => {
    expect(detectBytes(zip([["xl/workbook.xml", enc("<workbook/>")]]))).toEqual({ fmt: "xlsx", reason: "" });
  });

  it("rejects Word, PowerPoint, other archives, corrupt archives and PDFs with the Python reasons", () => {
    expect(detectBytes(zip([["word/document.xml", enc("<d/>")]]))).toEqual({ fmt: "unsupported", reason: "Word document (.docx) is not a supported format" });
    expect(detectBytes(zip([["ppt/presentation.xml", enc("<p/>")]]))).toEqual({ fmt: "unsupported", reason: "PowerPoint (.pptx) is not a supported format" });
    expect(detectBytes(zip([["a.txt", enc("MSKU1234567")]]))).toEqual({ fmt: "unsupported", reason: "ZIP archive that is not an Excel workbook" });
    expect(detectBytes(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3, 4, 5, 6]))).toEqual({ fmt: "unsupported", reason: "corrupt ZIP archive" });
    expect(detectBytes(enc("%PDF-1.7 ..."))).toEqual({ fmt: "unsupported", reason: "PDF is not a supported format" });
  });

  it("lets text through", () => {
    expect(detectBytes(enc("MSKU1234567"))).toBeNull();
    expect(detectBytes(new Uint8Array([0xef, 0xbb, 0xbf, 0x41]))).toBeNull();
  });

  it("reads member names from the central directory", () => {
    expect(zipMemberNames(zip([["a/b.txt", enc("x")], ["c.txt", enc("y")]]))).toEqual(["a/b.txt", "c.txt"]);
  });
});

describe("content-based detection (dispatcher.detect_format)", () => {
  it.each([
    ["<?xml version='1.0'?><snx><container eqid='X'/></snx>", "snx"],
    ["<ns:snx xmlns:ns='u'/>", "snx"],
    ["<discharge><line-discharge-list unit-id='X'/></discharge>", "snx"],
    ["<root><unit unique-key='X'/></root>", "snx"],
    ["<root><item/></root>", "unsupported"],
    ["UNA:+.? 'UNB+UNOA:2+A+B'", "edifact"],
    ["﻿  UNB+UNOA:2+A+B'", "edifact"],
    ["hello UNH+1+COARRI:D:95B:UN' text", "edifact"],
    ["ISA*00*~ST*322*0001~", "x12"],
    ["GS*SO*A*B~ST*322~", "x12"],
    ["ST*322*0001 no terminator", "txt"],
    ["just a list MSKU1234567", "txt"],
    ["", "txt"],
  ])("%s -> %s", (text, fmt) => {
    expect(detectFormat(text).fmt).toBe(fmt);
  });

  it("explains an unrecognized XML schema", () => {
    expect(detectFormat("<root/>").reason).toBe("XML detected but not a recognized container XML schema");
  });
});

describe("byte decoding mirrors the service", () => {
  it("keeps a UTF-8 byte order mark and round-trips", () => {
    const bytes = new Uint8Array([0xef, 0xbb, 0xbf, ...enc("MSKU1234567\n")]);
    const { text, encoding } = decodeBytes(bytes);
    expect(encoding).toBe("utf-8");
    expect(text.charCodeAt(0)).toBe(0xfeff);
    expect(Buffer.from(encodeText(text, encoding)).equals(Buffer.from(bytes))).toBe(true);
  });

  it("falls back to ISO-8859-1 without remapping 0x80-0x9F", () => {
    const bytes = new Uint8Array([0x45, 0x51, 0x44, 0x80, 0x9f, 0xe9, 0x0a]);
    const { text, encoding } = decodeBytes(bytes);
    expect(encoding).toBe("latin-1");
    expect([...text].map((c) => c.charCodeAt(0))).toEqual([0x45, 0x51, 0x44, 0x80, 0x9f, 0xe9, 0x0a]);
    expect(Buffer.from(encodeText(text, encoding)).equals(Buffer.from(bytes))).toBe(true);
  });
});
