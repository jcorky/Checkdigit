import { describe, expect, it } from "vitest";

import { BULK_CAP, BULK_COLUMNS, extractTokens, rowRecord, summarize, toCsv, toJson, validateEntries } from "../src/lib/bulk";

describe("list validation mode accounts for every supplied entry", () => {
  it("produces a row for each entry, including unrecognized ones", () => {
    const res = validateEntries("CSQU3054383\nCSQU30543X3\nwrong");
    expect(res.rows.map((r) => r.as_found)).toEqual(["CSQU3054383", "CSQU30543X3", "wrong"]);
    const structure = res.rows.map((r) => r.validation.layers.find((l) => l.layer === "structure")?.state);
    expect(structure).toEqual(["passed", "failed", "failed"]);
    const s = summarize(res.rows);
    expect(s).toMatchObject({ total: 3, check_passed: 1, structure_failed: 2 });
  });

  it("preserves source line and original text", () => {
    const res = validateEntries("a\n\n  CSQU3054383  \nx,y");
    expect(res.rows.map((r) => [r.line, r.as_found])).toEqual([
      [1, "a"],
      [3, "CSQU3054383"],
      [4, "x"],
      [4, "y"],
    ]);
  });

  it("refuses above the cap rather than silently truncating", () => {
    const many = Array.from({ length: BULK_CAP + 3 }, () => "wrong").join("\n");
    const res = validateEntries(many);
    expect(res.refused).toEqual({ count: BULK_CAP + 3, cap: BULK_CAP });
  });
});

describe("extraction mode reports what it set aside", () => {
  it("counts cells that held no identifier and samples them", () => {
    const res = extractTokens("ref CSQU3054383 here\nnote: nothing\nMSKU1234565");
    expect(res.rows.map((r) => r.normalized)).toEqual(["CSQU3054383", "MSKU1234565"]);
    expect(res.excluded).toEqual({ count: 1, samples: ["note: nothing"] });
  });
  it("has no excluded record when every cell yields a token", () => {
    expect(extractTokens("CSQU3054383\nMSKU1234565").excluded).toBeNull();
  });
});

describe("several-number paste", () => {
  it("extracts one token per line or cell and normalizes visibly", () => {
    const res = extractTokens("CSQU3054383\nmsku 123456 7, APLU100000;21 81 2471 217-3\n\n  \nHELLO\n");
    expect(res.refused).toBeNull();
    expect(res.rows.map((r) => [r.line, r.as_found, r.normalized])).toEqual([
      [1, "CSQU3054383", "CSQU3054383"],
      [2, "msku 123456 7", "MSKU1234567"],
      [2, "APLU100000", "APLU100000"],
      [2, "21 81 2471 217-3", "218124712173"],
    ]);
    expect(res.cells_seen).toBe(5);
  });

  it("finds concatenated numbers inside one cell and keeps the cell text", () => {
    const res = extractTokens("APLU3280480MSKU4570605APLU8266451");
    expect(res.rows.map((r) => r.normalized)).toEqual(["APLU3280480", "MSKU4570605", "APLU8266451"]);
    expect(res.rows.every((r) => r.as_found === r.normalized)).toBe(true);
  });

  it("summarizes by layer outcome, never as one green badge", () => {
    const res = extractTokens("CSQU3054383\nMSKU1234567\nAPLU100000\nAPLX1234560\n218124712173");
    const s = summarize(res.rows);
    // APLX (unknown category) and the bare UIC number (no rail context) are flagged by the kernel
    expect(s).toMatchObject({ total: 5, check_passed: 2, check_failed: 2, computed: 1, structure_failed: 0, flagged: 2 });
    const rec = rowRecord(res.rows[1] as never);
    expect(rec).toMatchObject({ as_found: "MSKU1234567", status: "corrected", printed_check: "7", computed_check: "5", suggested: "MSKU1234565" });
    expect(rowRecord(res.rows[2] as never).status).toBe("computed");
    expect(rowRecord(res.rows[3] as never).status).toBe("flagged");
  });

  it("refuses loudly above the cap with the count", () => {
    const many = Array.from({ length: BULK_CAP + 5 }, (_, i) => `MSKU${String(i).padStart(6, "0")}0`).join("\n");
    const res = extractTokens(many);
    expect(res.refused).toEqual({ count: BULK_CAP + 5, cap: BULK_CAP });
  });

  it("exports CSV with a BOM, CRLF and the fixed column set, and JSON with layers", () => {
    const res = extractTokens("MSKU1234567");
    const csv = toCsv(res.rows);
    expect(csv.startsWith("﻿" + BULK_COLUMNS.join(","))).toBe(true);
    expect(csv.split("\r\n")).toHaveLength(3);
    expect(csv).toContain("MSKU1234567,MSKU1234567,iso6346,corrected,7,5,MSKU1234565");
    const json = JSON.parse(toJson(res.rows)) as { layers: { layer: string }[] }[];
    expect(json[0]?.layers.map((l) => l.layer)).toContain("prefix_registration");
  });

  it("an empty paste yields no rows and an explicit nothing-to-check state", () => {
    const res = extractTokens("   \n\n");
    expect(res.rows).toEqual([]);
    expect(res.refused).toBeNull();
  });
});
