import { describe, expect, it } from "vitest";

import { buildResult } from "../src/lib/result-view";
import { validateEntries, sanitizeCell, toCsv } from "../src/lib/bulk";

/*
 * The published acceptance fixtures for the calculator. buildResult is the pure
 * core of both the home calculator and the /check page, so these lock the
 * observable result state for each input.
 */

describe("single-number acceptance fixtures", () => {
  const view = (s: string) => buildResult(s, "auto");

  it("never renders a stringified object in any state", () => {
    for (const s of ["", "CSQU30", "CSQU305438", "CSQU3054383", "CSQU3054384", "CSQX3054383", "CSQU3O54383", "218124712173", "nonsense"]) {
      expect(buildResult(s, "auto").cardHtml, s).not.toContain("[object Object]");
    }
  });

  it("CSQU305438 -> calculates 3, completes to CSQU3054383", () => {
    const v = view("CSQU305438");
    expect(v.kind).toBe("calculated");
    expect(v.explanation?.ok && v.explanation.computed).toBe(3);
    expect(v.explanation?.ok && v.explanation.full).toBe("CSQU3054383");
  });

  it("CSQU3054383 -> matches", () => {
    expect(view("CSQU3054383").kind).toBe("matching");
  });

  it("CSQU3054384 -> mismatch, entered 4, expected 3, suggests CSQU3054383", () => {
    const v = view("CSQU3054384");
    expect(v.kind).toBe("mismatch");
    expect(v.explanation?.ok && v.explanation.printed).toBe("4");
    expect(v.explanation?.ok && v.explanation.computed).toBe(3);
    expect(v.explanation?.ok && v.explanation.full).toBe("CSQU3054383");
    // the entered number is kept and the suggestion is not called a correction
    expect(v.cardHtml).toContain("CSQU3054384");
    expect(v.cardHtml).toContain("not a confirmed correction");
  });

  it("csqu 305438 3 -> normalises visibly and matches", () => {
    const v = view("csqu 305438 3");
    expect(v.normalized).toBe("CSQU3054383");
    expect(v.kind).toBe("matching");
  });

  it("BICU123456 -> calculates 5", () => {
    const v = view("BICU123456");
    expect(v.kind).toBe("calculated");
    expect(v.explanation?.ok && v.explanation.computed).toBe(5);
  });

  it("CSQU000007 -> calculates 0 from remainder 10", () => {
    const v = view("CSQU000007");
    expect(v.kind).toBe("calculated");
    const ex = v.explanation;
    expect(ex?.ok && ex.computed).toBe(0);
    expect(ex?.ok && ex.kind === "iso6346_ilu" && ex.remainder_ten).toBe(true);
  });

  it("CSQJ305438 -> calculates 6 (category J)", () => {
    expect(view("CSQJ305438").explanation).toMatchObject({ computed: 6 });
  });

  it("CSQZ305438 -> calculates 7 (category Z)", () => {
    expect(view("CSQZ305438").explanation).toMatchObject({ computed: 7 });
  });

  it("AAAU000000 -> calculates 7 and preserves every zero", () => {
    const v = view("AAAU000000");
    expect(v.kind).toBe("calculated");
    expect(v.explanation?.ok && v.explanation.full).toBe("AAAU0000007");
  });

  it("MSQU305438 -> calculates 3 (a checksum collision with CSQU305438)", () => {
    expect(view("MSQU305438").explanation).toMatchObject({ computed: 3 });
  });

  it("CSQX3054383 -> unsupported ISO category", () => {
    expect(view("CSQX3054383").kind).toBe("unsupported");
  });

  it("CSQU3O54383 -> invalid serial character; never substitutes zero", () => {
    const v = view("CSQU3O54383");
    expect(v.kind).toBe("invalid");
    // it must not silently turn the O into a 0 and present a completed number
    expect(v.cardHtml).not.toContain("CSQU3054383");
    expect(v.cardHtml).not.toContain("Copy full number");
    expect(v.cardHtml).not.toContain("Copy suggested");
  });
});

describe("the four-entry batch produces exactly four outcomes", () => {
  const res = validateEntries("CSQU3054383\nCSQU3054384\nCSQU305438\nBAD");
  const stateOf = (i: number, layer: string) =>
    res.rows[i]?.validation.layers.find((l) => l.layer === layer)?.state;

  it("has one row per entry", () => {
    expect(res.rows).toHaveLength(4);
    expect(res.rows.map((r) => r.as_found)).toEqual(["CSQU3054383", "CSQU3054384", "CSQU305438", "BAD"]);
  });

  it("classifies them match, mismatch, calculated, invalid", () => {
    expect(stateOf(0, "check_digit")).toBe("passed");
    expect(stateOf(1, "check_digit")).toBe("failed");
    expect(stateOf(2, "check_digit")).toBe("insufficient_information");
    expect(stateOf(3, "structure")).toBe("failed");
  });
});

describe("CSV export is spreadsheet-injection safe without losing data", () => {
  it("neutralises a formula-leading field but keeps its text", () => {
    expect(sanitizeCell("=1+1")).toBe("'=1+1");
    expect(sanitizeCell("+cmd")).toBe("'+cmd");
    expect(sanitizeCell("-2")).toBe("'-2");
    expect(sanitizeCell("@x")).toBe("'@x");
    expect(sanitizeCell("CSQU3054383")).toBe("CSQU3054383");
  });

  it("prefixes a dangerous cell in the exported CSV, original text intact", () => {
    const csv = toCsv(validateEntries("=cmd").rows);
    expect(csv).toContain("'=cmd");
  });
});
