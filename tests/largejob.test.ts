import { describe, expect, it } from "vitest";

import { correctIdentifier, FieldContext, Status } from "../src/lib/checkdigit";
import {
  countMatching,
  decideMatching,
  decisionsDigest,
  exportCorrected,
  MemorySink,
  MemoryStore,
  readPage,
  runStream,
  writeExceptions,
  writeLedger,
  type StoredRecord,
} from "../src/lib/largejob";
import { bytesSource } from "../src/lib/stream";

// A deterministic generator: valid numbers, single wrong check digits, an
// invalid structure, an empty field, a size type and free text that must not
// be touched. Expected output is computed independently of the streaming path.
const OWNERS = ["MSKU", "CSQU", "TCLU", "HLXU", "OOLU", "CMAU"];

function bic(seed: number): string {
  const owner = OWNERS[seed % OWNERS.length] as string;
  const serial = String((seed * 7919) % 1000000).padStart(6, "0");
  const res = correctIdentifier(owner + serial + "0", FieldContext.EQUIPMENT_ID);
  return owner + serial + res.computed_check;
}

interface Row { id: string; expectedOut: string; kind: "valid" | "corrected" | "flagged" | "invalid_structure" | "missing" | "not_a_target" }

// The kernel classifies each token; the test checks that the streaming path
// counts, stores and splices exactly what the kernel decided.
function row(id: string): Row {
  if (id === "") return { id, expectedOut: "", kind: "missing" };
  const res = correctIdentifier(id, FieldContext.EQUIPMENT_ID);
  return { id, expectedOut: res.status === Status.CORRECTED ? (res.corrected as string) : id, kind: res.status as Row["kind"] };
}

function rows(n: number): Row[] {
  const out: Row[] = [];
  for (let i = 0; i < n; i++) {
    const good = bic(i);
    switch (i % 7) {
      case 0:
      case 1:
      case 2:
        out.push(row(good));
        break;
      case 3:
        out.push(row(good.slice(0, 10) + String((Number(good[10]) + 1) % 10)));
        break;
      case 4:
        out.push(row(""));
        break;
      case 5:
        out.push(row("MSKU12"));
        break;
      default:
        out.push(row("22G1"));
    }
  }
  const kinds = new Set(out.map((r) => r.kind));
  for (const k of ["valid", "corrected", "missing", "invalid_structure"]) if (!kinds.has(k as Row["kind"])) throw new Error(`fixture lacks ${k}`);
  return out;
}

function csvOf(rs: Row[], pick: (r: Row) => string, extra = "café 北京"): string {
  const lines = ["record_id,note,container,qty"];
  rs.forEach((r, i) => lines.push(`${i + 1},"${extra}, quoted",${pick(r)},${i % 3}`));
  return lines.join("\r\n") + "\r\n";
}

const enc = new TextEncoder();

describe("large-file streaming job", () => {
  const rs = rows(700);
  const inputText = csvOf(rs, (r) => r.id);
  const expectedText = csvOf(rs, (r) => r.expectedOut);
  const tally = (k: Row["kind"]) => rs.filter((r) => r.kind === k).length;

  for (const chunk of [113, 4096, 1 << 20]) {
    it(`csv: counts, decisions and spliced export are exact with ${chunk}-byte chunks`, async () => {
      const store = new MemoryStore();
      const source = bytesSource(enc.encode(inputText));
      const progress: number[] = [];
      const result = await runStream(source, store, { columns: ["container"], chunk_bytes: chunk, progress_every_bytes: 1, onProgress: (p) => progress.push(p.counts.bytes_done) });
      expect(result.format).toBe("csv");
      expect(result.encoding).toBe("utf-8");
      expect(result.delimiter).toBe(",");
      expect(result.counts.records).toBe(rs.length);
      expect(result.counts.valid).toBe(tally("valid"));
      expect(result.counts.corrected).toBe(tally("corrected"));
      expect(result.counts.missing).toBe(tally("missing"));
      expect(result.counts.invalid_structure).toBe(tally("invalid_structure"));
      expect(result.counts.not_a_target).toBe(tally("not_a_target"));
      expect(result.counts.stored).toBe(tally("corrected") + tally("missing") + tally("invalid_structure"));
      expect(store.count).toBe(result.counts.stored);
      expect(progress[progress.length - 1]).toBe(source.size);

      const page = await readPage(store, 0, 5, { kind: "corrected" });
      expect(page.items).toHaveLength(5);
      for (const item of page.items as (StoredRecord & { decision: string })[]) {
        expect(item.kind).toBe("corrected");
        expect(item.decision).toBe("proposed");
        expect(inputText.slice(item.offset, item.offset + item.raw.length)).toBe(item.raw);
        expect(item.candidate).toBe(rs[item.record_no - 1]!.expectedOut);
      }

      const before = await countMatching(store, { kind: "corrected" });
      expect(before.proposals).toBe(tally("corrected"));
      await expect(decideMatching(store, { kind: "corrected" }, "approved", before.proposals - 1)).rejects.toThrow(/SELECTION_DRIFT/);
      const approval = await decideMatching(store, { kind: "corrected" }, "approved", before.proposals);
      expect(approval.count).toBe(tally("corrected"));
      expect(approval.digest).toMatch(/^[0-9a-f]{64}$/);
      await expect(decideMatching(store, { kind: "corrected" }, "approved", null)).rejects.toThrow(/EMPTY_SELECTION/);
      expect((await countMatching(store, { decision: "approved" })).count).toBe(tally("corrected"));
      expect(await decisionsDigest(store)).toMatch(/^[0-9a-f]{64}$/);

      const sink = new MemorySink();
      const exported = await exportCorrected(source, result.encoding, store, sink, { chunk_bytes: chunk });
      expect(exported.edits_applied).toBe(tally("corrected"));
      const outText = new TextDecoder().decode(sink.bytes());
      expect(outText).toBe(expectedText);
      expect(exported.bytes_written).toBe(sink.bytes().length);
      expect(exported.exceptions).toBe(store.count - tally("corrected"));

      // idempotence: the corrected output proposes nothing
      const again = new MemoryStore();
      const r2 = await runStream(bytesSource(sink.bytes()), again, { columns: ["container"], chunk_bytes: chunk });
      expect(r2.counts.corrected).toBe(0);
      expect(r2.counts.valid).toBe(tally("valid") + tally("corrected"));

      const exc = new MemorySink();
      expect(await writeExceptions(store, exc)).toBe(store.count - tally("corrected"));
      const excText = new TextDecoder().decode(exc.bytes());
      expect(excText.split("\r\n")[0]).toBe("index,location,raw,kind,decision,candidate");
      const led = new MemorySink();
      expect(await writeLedger(store, led)).toBe(tally("corrected"));
      expect(new TextDecoder().decode(led.bytes()).split("\r\n").filter(Boolean)).toHaveLength(tally("corrected") + 1);
    });
  }

  it("exports a multi-megabyte file so the block digest spans several blocks and stays boundary-independent", async () => {
    const big = rows(90000);
    const text = csvOf(big, (r) => r.id);
    const expected = csvOf(big, (r) => r.expectedOut);
    const bytes = enc.encode(text);
    expect(bytes.length).toBeGreaterThan(3 * 1024 * 1024);
    const digests: string[] = [];
    for (const chunk of [777_777, 4 * 1024 * 1024]) {
      const store = new MemoryStore();
      const source = bytesSource(bytes);
      const result = await runStream(source, store, { columns: ["container"], chunk_bytes: chunk });
      await decideMatching(store, { kind: "corrected" }, "approved", result.counts.corrected);
      const sink = new MemorySink();
      const exported = await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: chunk });
      expect(exported.edits_applied).toBe(big.filter((r) => r.kind === "corrected").length);
      expect(new TextDecoder().decode(sink.bytes())).toBe(expected);
      expect(exported.sha256).toMatch(/^blocks-[0-9a-f]{64}$/);
      digests.push(exported.sha256);
    }
    expect(digests[0]).toBe(digests[1]);
  });

  it("rejected and deferred proposals are not spliced; deferred ones can be decided later", async () => {
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(inputText));
    await runStream(source, store, { columns: ["container"], chunk_bytes: 4096 });
    const total = (await countMatching(store, { kind: "corrected" })).proposals;
    await decideMatching(store, { kind: "corrected" }, "deferred", total);
    expect((await countMatching(store, { kind: "corrected" }, ["proposed"])).proposals).toBe(0);
    const sink = new MemorySink();
    const exported = await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: 4096 });
    expect(exported.edits_applied).toBe(0);
    expect(new TextDecoder().decode(sink.bytes())).toBe(inputText);
    await decideMatching(store, { kind: "corrected", decision: "deferred" }, "rejected", total);
    expect((await countMatching(store, { decision: "rejected" })).count).toBe(total);
  });

  it("falls back to latin-1 when the file is not UTF-8 and exports the same bytes elsewhere", async () => {
    const rs2 = rows(50);
    const text = csvOf(rs2, (r) => r.id, "café ");
    const bytes = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
    const store = new MemoryStore();
    const result = await runStream(bytesSource(bytes), store, { columns: ["container"], chunk_bytes: 97 });
    expect(result.encoding).toBe("latin-1");
    expect(result.counts.corrected).toBe(rs2.filter((r) => r.kind === "corrected").length);
    await decideMatching(store, { kind: "corrected" }, "approved", result.counts.corrected);
    const sink = new MemorySink();
    await exportCorrected(bytesSource(bytes), "latin-1", store, sink, { chunk_bytes: 97 });
    const out = sink.bytes();
    const expected = csvOf(rs2, (r) => r.expectedOut, "café ");
    expect(out.length).toBe(expected.length);
    for (let i = 0; i < out.length; i++) if (out[i] !== expected.charCodeAt(i)) throw new Error(`byte ${i} differs`);
  });

  it("edifact: EQD+CN identifiers behind a UNA header with release characters", async () => {
    const good = bic(11);
    const wrong = good.slice(0, 10) + String((Number(good[10]) + 3) % 10);
    const edi = `UNA:+.? 'UNB+UNOA:2+A+B+260916:1200+1'UNH+1+COARRI:D:95B:UN'FTX+AAA+++plus?+sign'EQD+CN+${wrong}+22G1'EQD+CN+${good}'EQD+CN+'UNT+5+1'UNZ+1+1'`;
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(edi));
    const result = await runStream(source, store, { chunk_bytes: 13 });
    expect(result.format).toBe("edifact");
    expect(result.counts).toMatchObject({ valid: 1, corrected: 1, missing: 1, records: 3 });
    await decideMatching(store, {}, "approved", 1);
    const sink = new MemorySink();
    await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: 13 });
    expect(new TextDecoder().decode(sink.bytes())).toBe(edi.replace(wrong, good));
  });

  it("x12: N7 check digit slot, absent N7-18 and N9*EQ", async () => {
    const good = bic(5);
    const initial = good.slice(0, 4);
    const number = good.slice(4, 10);
    const check = good[10] as string;
    const wrongCheck = String((Number(check) + 1) % 10);
    const isa = "ISA*00*          *00*          *ZZ*TERM           *ZZ*RAIL           *260916*1200*U*00401*000000001*0*P*:~";
    const n7 = (c: string | null) => `N7*${initial}*${number}${c === null ? "" : "*".repeat(16) + c}~`;
    const x12 = `${isa}GS*IO*TERM*RAIL~ST*322*0001~${n7(wrongCheck)}${n7(null)}${n7("")}N9*EQ*${good.slice(0, 10) + wrongCheck}~SE*6*0001~GE*1*1~IEA*1*000000001~`;
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(x12));
    const result = await runStream(source, store, { chunk_bytes: 29 });
    expect(result.format).toBe("x12");
    expect(result.counts).toMatchObject({ corrected: 3, flagged: 1, valid: 0 });
    const page = await readPage(store, 0, 10, {});
    const flagged = page.items.find((i) => i.kind === "flagged");
    expect(flagged?.x12_slot).toBe(true);
    await decideMatching(store, {}, "approved", 3);
    const sink = new MemorySink();
    await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: 29 });
    const expected = `${isa}GS*IO*TERM*RAIL~ST*322*0001~${n7(check)}${n7(null)}${n7(check)}N9*EQ*${good}~SE*6*0001~GE*1*1~IEA*1*000000001~`;
    expect(new TextDecoder().decode(sink.bytes())).toBe(expected);
  });

  it("xml: container and unit identifiers across comments and CDATA", async () => {
    const good = bic(21);
    const wrong = good.slice(0, 10) + String((Number(good[10]) + 2) % 10);
    const xml = `<?xml version="1.0"?>\n<list><!-- <container eqid="${wrong}"/> --><![CDATA[<unit id="${wrong}"/>]]>\n<container eqid="${wrong}" note="a &gt; b"/>\n<unit id="${good}" unique-key="${wrong}"/><unit id=""/></list>\n`;
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(xml));
    const result = await runStream(source, store, { chunk_bytes: 17 });
    expect(result.format).toBe("xml");
    expect(result.counts).toMatchObject({ corrected: 2, valid: 1, missing: 1, records: 3 });
    await decideMatching(store, {}, "approved", 2);
    const sink = new MemorySink();
    await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: 17 });
    const expected = `<?xml version="1.0"?>\n<list><!-- <container eqid="${wrong}"/> --><![CDATA[<unit id="${wrong}"/>]]>\n<container eqid="${good}" note="a &gt; b"/>\n<unit id="${good}" unique-key="${good}"/><unit id=""/></list>\n`;
    expect(new TextDecoder().decode(sink.bytes())).toBe(expected);
  });

  it("txt: identifiers in free text are corrected in place and prose is untouched", async () => {
    const good = bic(31);
    const wrong = good.slice(0, 10) + String((Number(good[10]) + 4) % 10);
    const other = bic(40);
    const txt = `Gate log é\r\nunit ${wrong} arrived; ${good} departed\nsize 22G1 and ${other} no\r\n`;
    const untrusted = new MemoryStore();
    const flaggedOnly = await runStream(bytesSource(enc.encode(txt)), untrusted, { chunk_bytes: 9 });
    // free-text context: a wrong check digit is flagged for review, never corrected
    expect(flaggedOnly.counts).toMatchObject({ corrected: 0, flagged: 1, valid: 2 });
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(txt));
    const result = await runStream(source, store, { chunk_bytes: 9, trust: true });
    expect(result.format).toBe("txt");
    expect(result.counts.corrected).toBe(1);
    expect(result.counts.valid).toBe(2);
    await decideMatching(store, {}, "approved", 1);
    const sink = new MemorySink();
    await exportCorrected(source, "utf-8", store, sink, { chunk_bytes: 9 });
    expect(new TextDecoder().decode(sink.bytes())).toBe(txt.replace(wrong, good));
  });

  it("export refuses a source whose bytes no longer match the stored raw span", async () => {
    const store = new MemoryStore();
    const source = bytesSource(enc.encode(inputText));
    await runStream(source, store, { columns: ["container"] });
    const n = (await countMatching(store, { kind: "corrected" })).proposals;
    await decideMatching(store, { kind: "corrected" }, "approved", n);
    const tampered = bytesSource(enc.encode(inputText.replace(rs[3]!.id, "XXXX0000000")));
    await expect(exportCorrected(tampered, "utf-8", store, new MemorySink())).rejects.toThrow(/EDIT_MISMATCH/);
  });

  it("cancellation stops the pass and reports it", async () => {
    const store = new MemoryStore();
    const signal = { cancelled: false };
    const result = await runStream(bytesSource(enc.encode(inputText)), store, { columns: ["container"], chunk_bytes: 256, progress_every_bytes: 1,
      signal, onProgress: () => { signal.cancelled = true; } });
    expect(result.cancelled).toBe(true);
    expect(result.counts.records).toBeLessThan(rs.length);
  });

  it("csv without a mapping is refused with the available columns", async () => {
    const store = new MemoryStore();
    await expect(runStream(bytesSource(enc.encode(inputText)), store, { columns: ["nope"] })).rejects.toThrow(/column "nope" not found/);
    await expect(runStream(bytesSource(enc.encode(inputText)), store, {})).rejects.toThrow(/column mapping/);
  });

  it("status vocabulary matches the kernel", () => {
    expect(Object.values(Status)).toEqual(expect.arrayContaining(["valid", "corrected", "flagged", "invalid_structure", "not_a_target"]));
  });
});
