import { describe, expect, it } from "vitest";

import {
  analyzeText,
  backspace,
  cellsFrom,
  editCell,
  emptyCells,
  evaluate,
  replaceAll,
  tokenOf,
} from "../src/lib/segmented";

const PREFILL = cellsFrom("CSQU3054383");

describe("whole-identifier paste replaces every cell", () => {
  it("pasting a ten-character body over a full identifier clears the old check digit", () => {
    const r = editCell(PREFILL, 0, "APLU100000");
    expect(r.kind).toBe("replace");
    expect(tokenOf(r.cells)).toBe("APLU100000");
    expect(r.cells[10]).toBe("");
    const vm = evaluate(r.cells);
    expect(vm.state).toBe("computed");
    expect(vm.computedCheck).toBe("0");
    expect(vm.anatomy.check).toBe("0");
  });

  it("pasting into a middle cell still replaces the whole identifier when it is complete", () => {
    const r = editCell(PREFILL, 5, "MSKU1234565");
    expect(r.kind).toBe("replace");
    expect(tokenOf(r.cells)).toBe("MSKU1234565");
    expect(evaluate(r.cells).state).toBe("valid");
  });

  it("typing one character edits only that cell", () => {
    const r = editCell(PREFILL, 10, "5");
    expect(r.kind).toBe("typed");
    expect(tokenOf(r.cells)).toBe("CSQU3054385");
    expect(r.focus).toBe(10);
    expect(evaluate(r.cells).state).toBe("corrected");
    expect(evaluate(r.cells).computedCheck).toBe("3");
  });

  it("a segment paste fills consecutive cells and keeps the rest", () => {
    const r = editCell(PREFILL, 4, "999999");
    expect(r.kind).toBe("segment");
    expect(tokenOf(r.cells)).toBe("CSQU9999993");
    expect(r.focus).toBe(10);
  });

  it("a segment paste that does not fit is refused, not truncated", () => {
    const r = editCell(PREFILL, 8, "12345");
    expect(r.kind).toBe("overflow");
    expect(r.cells).toEqual(PREFILL);
    expect(r.notice?.level).toBe("error");
    expect(r.notice?.text).toContain("only 3 fit");
  });

  it("more than eleven characters is refused, not truncated", () => {
    const r = editCell(PREFILL, 0, "CSQU30543839");
    expect(r.kind).toBe("overflow");
    expect(r.cells).toEqual(PREFILL);
    expect(r.notice?.text).toContain("12 identifier characters");
  });
});

describe("normalization is approved, visible, never silent", () => {
  it("removes whitespace and hyphens and upper-cases, and says so", () => {
    const r = replaceAll(emptyCells(), "msku 123456-5");
    expect(r.kind).toBe("replace");
    expect(tokenOf(r.cells)).toBe("MSKU1234565");
    expect(r.notice?.level).toBe("info");
    expect(r.notice?.text).toContain("removed 2 separators");
    expect(r.notice?.text).toContain("upper-cased 4 letters");
    expect(r.notice?.text).toContain('"msku 123456-5"');
  });

  it("refuses punctuation and offers the normalized form as a proposal", () => {
    const r = replaceAll(PREFILL, "MSKU.123456.5");
    expect(r.kind).toBe("rejected");
    expect(r.cells).toEqual(PREFILL);
    expect(r.notice?.level).toBe("error");
    expect(r.notice?.text).toContain('"." ×2');
    expect(r.notice?.text).toContain('Original kept: "MSKU.123456.5"');
    expect(r.notice?.proposal).toBe("MSKU1234565");
    const accepted = replaceAll(PREFILL, r.notice?.proposal ?? "");
    expect(tokenOf(accepted.cells)).toBe("MSKU1234565");
  });

  it("confusable characters are not substituted", () => {
    const n = analyzeText("MSKU1234565");
    expect(n.accepted).toBe("MSKU1234565");
    expect(n.rejected).toEqual([]);
    const r = replaceAll(emptyCells(), "MSKU12345é5");
    expect(r.kind).toBe("rejected");
    expect(r.notice?.proposal).toBe("MSKU123455");
  });

  it("typing a separator into a cell is a no-op", () => {
    const r = editCell(PREFILL, 3, " ");
    expect(r.kind).toBe("noop");
    expect(r.cells).toEqual(PREFILL);
  });
});

describe("clear, backspace and gaps", () => {
  it("clearing empties every cell and evaluates as empty", () => {
    const r = replaceAll(PREFILL, "");
    expect(r.kind).toBe("cleared");
    expect(r.cells).toEqual(emptyCells());
    expect(evaluate(r.cells).state).toBe("empty");
  });

  it("backspace clears the cell, or the previous one when already empty", () => {
    const a = backspace(PREFILL, 10);
    expect(a.cells[10]).toBe("");
    expect(a.focus).toBe(10);
    const b = backspace(a.cells, 10);
    expect(b.cells[9]).toBe("");
    expect(b.focus).toBe(9);
  });

  it("a hole in the middle is reported as a gap, not judged", () => {
    const cells = cellsFrom("CSQU3054383");
    cells[5] = "";
    expect(evaluate(cells).state).toBe("gap");
  });

  it("a short identifier reports how many characters are missing", () => {
    const vm = evaluate(cellsFrom("CSQU30"));
    expect(vm.state).toBe("partial");
    expect(vm.missing).toBe(4);
  });
});

describe("verdicts come from the kernel", () => {
  it.each([
    ["CSQU3054383", "valid", "3"],
    ["CSQU3054385", "corrected", "3"],
    ["APLX1234560", "flagged", "3"],
    ["L01U1150107", "flagged", "5"],
    ["1234567890A", "invalid", null],
  ])("%s → %s", (token, state, check) => {
    const vm = evaluate(cellsFrom(token));
    expect(vm.state).toBe(state);
    expect(vm.computedCheck).toBe(check);
  });

  it("ten characters of the wrong shape are invalid", () => {
    expect(evaluate(cellsFrom("123456789A")).state).toBe("invalid");
  });
});
