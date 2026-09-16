import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { correct, correctWithHint } from "../src/lib/dispatcher";
import { decodeBytes, encodeText } from "../src/lib/encoding";
import { correctEdifact } from "../src/lib/formats/edifact";
import { correctSnx } from "../src/lib/formats/snx";
import { correctTxt } from "../src/lib/formats/txt";
import { correctX12 } from "../src/lib/formats/x12";
import { Policy } from "../src/lib/policy";
import { summary, type CorrectionReport } from "../src/lib/report";
import { loadVectors } from "./helpers/vectors";

const pyDir = resolve(import.meta.dirname, "..", "checkdigit");
const bytesOf = (rel: string): Uint8Array => {
  try {
    return new Uint8Array(readFileSync(resolve(pyDir, rel)));
  } catch (err) {
    throw new Error(`golden fixture missing: ${rel} (${String(err)})`);
  }
};

function goldenPair(input: string, golden: string, run: (text: string) => CorrectionReport): void {
  const inBytes = bytesOf(input);
  const goldBytes = bytesOf(golden);
  const { text, encoding } = decodeBytes(inBytes);
  const report = run(text);
  const out = encodeText(report.corrected_text as string, encoding);
  expect(out.length, "output length equals input length").toBe(inBytes.length);
  expect(Buffer.from(out).equals(Buffer.from(goldBytes)), `output bytes equal ${golden}`).toBe(true);
  // idempotence: correcting the corrected output changes nothing
  const again = run(report.corrected_text as string);
  expect(again.corrected).toEqual([]);
  expect(again.corrected_text).toBe(report.corrected_text);
  expect(report.offset_kind).toBe("utf16_unit");
}

describe("golden byte parity with the Python correctors", () => {
  it("SNX: 4Containers_snx_Example.xml", () => {
    goldenPair("samples/4Containers_snx_Example.xml", "4Containers_snx_Example.CORRECTED.xml", (t) => correctSnx(t));
    const rep = correctSnx(decodeBytes(bytesOf("samples/4Containers_snx_Example.xml")).text);
    expect(summary(rep)).toMatchObject({ containers: 4, corrected: 3, valid: 1, flagged: 0 });
    expect(rep.corrected.map((c) => c.occurrences.length)).toEqual([4, 4, 4]);
  });

  it("EDIFACT: USER01_baplie_edi.txt (strict)", () => {
    goldenPair("samples/USER01_baplie_edi.txt", "USER01_baplie_edi.CORRECTED.txt", (t) => correctEdifact(t));
    const rep = correctEdifact(decodeBytes(bytesOf("samples/USER01_baplie_edi.txt")).text);
    expect(rep.corrected.length).toBe(10);
  });

  it("EDIFACT: L02LOADLIST.txt under owner_policy=lenient", () => {
    goldenPair("samples/L02LOADLIST.txt", "L02LOADLIST.CORRECTED_lenient.txt", (t) => correctEdifact(t, { owner_policy: "lenient" }));
    const text = decodeBytes(bytesOf("samples/L02LOADLIST.txt")).text;
    const lenient = correctEdifact(text, { owner_policy: "lenient" });
    expect(summary(lenient)).toMatchObject({ containers: 93, corrected: 90, valid: 3 });
    const strict = correctEdifact(text);
    expect(strict.corrected).toEqual([]);
    expect(strict.flagged.length).toBe(93);
    expect(strict.corrected_text).toBe(text);
  });

  it("X12: run_pass6 synthetic 322 -> X12_synthetic.CORRECTED.edi", () => {
    const synth =
      "ST*322*0001~\nN7*MSCU*123456****************0****22G1~\nN7*TCLU*456789********************45G1~\n" +
      "N7*HLBU*112233~\nN9*EQ*MSCU1234560~\nN9*BM*SSLMSCU1234560001~\nSE*6*0001~\n";
    const golden = decodeBytes(bytesOf("X12_synthetic.CORRECTED.edi")).text;
    const rep = correctX12(synth);
    expect(rep.corrected_text).toBe(golden);
    // An empty N7-18 slot receives an inserted digit, so this output is one
    // character longer than its input; every other byte is unchanged.
    expect(rep.corrected_text?.length).toBe(synth.length + 1);
    expect(rep.corrected_text?.length).toBe(golden.length);
    expect(rep.corrected.map((c) => c.new)).toEqual(["MSCU1234566", "TCLU4567897", "MSCU1234566"]);
    expect(rep.flagged.map((f) => f.eqid)).toEqual(["HLBU112233"]);
    expect(rep.corrected_text).toContain("N9*BM*SSLMSCU1234560001~");
    expect(correctX12(golden).corrected).toEqual([]);
  });

  it("plain text: txtcontainers.txt has 41 distinct numbers, 3 valid, 38 flagged in free text", () => {
    const text = decodeBytes(bytesOf("samples/txtcontainers.txt")).text;
    const rep = correctTxt(text, { trust: false });
    expect(summary(rep)).toMatchObject({ containers: 41, valid: 3, flagged: 38, corrected: 0 });
    expect(rep.corrected_text).toBe(text);
    const trusted = correctTxt(text, { trust: true });
    expect(summary(trusted)).toMatchObject({ containers: 41, valid: 3, corrected: 38 });
    expect(correctTxt(trusted.corrected_text as string, { trust: true }).corrected).toEqual([]);
  });

  it("SNX: COPRAR_Discharge.xml has 30 units", () => {
    const text = decodeBytes(bytesOf("samples/COPRAR_Discharge.xml")).text;
    const [det, rep] = correct(text);
    expect(det.fmt).toBe("snx");
    expect(rep?.total_containers).toBe(30);
  });
});

interface FormatVector {
  name: string;
  text: string;
  trust?: boolean;
  owner_policy?: string;
  format_hint?: string;
  parse_options?: Record<string, unknown>;
  policy?: { deny?: string[]; allow?: string[]; per_prefix?: Record<string, string>; default_policy?: string };
  expected: {
    error?: string;
    message?: string;
    detected?: { fmt: string; reason: string };
    report?: Record<string, unknown> | null;
    summary?: Record<string, number>;
  };
}

function codepointToUtf16(text: string, index: number): number {
  let cp = 0;
  let unit = 0;
  for (const ch of text) {
    if (cp === index) return unit;
    cp++;
    unit += ch.length;
  }
  return unit;
}

/** "line L:C" labels count columns in the same units as offsets; translate them too. */
function translateLabel(label: string, text: string): string {
  const m = /^line (\d+):(\d+)$/.exec(label);
  if (!m) return label;
  const lineText = text.split("\n")[Number(m[1]) - 1] ?? "";
  return `line ${m[1]}:${codepointToUtf16(lineText, Number(m[2]) - 1) + 1}`;
}

function translateOffsets(report: Record<string, unknown>, text: string): Record<string, unknown> {
  const out = JSON.parse(JSON.stringify(report)) as Record<string, unknown>;
  for (const change of out["corrected"] as { occurrences: { offset: number; label: string }[] }[]) {
    for (const occ of change.occurrences) {
      occ.offset = codepointToUtf16(text, occ.offset);
      occ.label = translateLabel(occ.label, text);
    }
  }
  for (const flag of out["flagged"] as { offset: number }[]) {
    if (flag.offset >= 0) flag.offset = codepointToUtf16(text, flag.offset);
  }
  return out;
}

describe("format vectors (scripts/gen_format_vectors.py)", () => {
  const vectors = loadVectors<FormatVector[]>("format_vectors.json");
  const errorMap: Record<string, string> = {
    XmlSecurityError: "XmlSecurityError",
    ParseError: "XmlParseError",
    IntegrityError: "IntegrityError",
  };

  for (const v of vectors) {
    it(v.name, () => {
      const policy = v.policy ? new Policy(v.policy) : null;
      const options = { owner_policy: v.owner_policy ?? "strict", trust: v.trust ?? false, policy };
      let result: ReturnType<typeof correct>;
      try {
        result = v.format_hint
          ? correctWithHint(v.text, v.format_hint, (v.parse_options ?? {}) as never, options)
          : correct(v.text, options);
      } catch (err) {
        const name = (err as Error).name;
        expect(v.expected.error, `TypeScript threw ${name}: ${(err as Error).message}`).toBeDefined();
        expect(name).toBe(errorMap[v.expected.error as string] ?? v.expected.error);
        return;
      }
      expect(v.expected.error, `Python raised ${v.expected.error}: ${v.expected.message}`).toBeUndefined();
      const [det, rep] = result;
      expect(det).toEqual(v.expected.detected);
      if (v.expected.report === null) {
        expect(rep).toBeNull();
        return;
      }
      expect(rep).not.toBeNull();
      const got = { ...(rep as CorrectionReport) } as Record<string, unknown>;
      delete got["offset_kind"];
      got["containers"] = (rep as CorrectionReport).containers.map((c) => {
        const { near_misses: _nm, ...rest } = c;
        return rest;
      });
      // Python offsets are code-point indices; this port reports UTF-16 indices.
      // Translate the expected offsets through the fixture text before comparing.
      const expected = translateOffsets(v.expected.report as Record<string, unknown>, v.text);
      expect(got).toEqual(expected);
      expect(summary(rep as CorrectionReport)).toEqual(v.expected.summary);
    });
  }
});
