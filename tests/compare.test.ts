import { describe, expect, it } from "vitest";

import { compare, compareCsv, outcomeLabel, sideFromText } from "../src/lib/compare";

describe("a side too large to read in full is reported as refused", () => {
  it("carries the refusal so the page can refuse rather than compare partial rows", () => {
    const big = Array.from({ length: 10_001 }, () => "CSQU3054383").join("\n");
    const side = sideFromText("Big feed", big);
    expect(side.refused).toEqual({ count: 10_001, cap: 10_000 });
    const ok = sideFromText("Small", "CSQU3054383\nMSKU1234565");
    expect(ok.refused).toBeNull();
  });
});

describe("outcome labels are descriptive and neutral", () => {
  it("names each membership outcome in plain language, repeating the list names", () => {
    expect(outcomeLabel("only_a", "Terminal feed", "Fleet master")).toBe("Only in Terminal feed");
    expect(outcomeLabel("only_b", "Terminal feed", "Fleet master")).toBe("Only in Fleet master");
    expect(outcomeLabel("both", "A", "B")).toBe("In both lists");
    expect(outcomeLabel("conflict", "A", "B")).toBe("Check-digit difference");
    expect(outcomeLabel("duplicate", "A", "B")).toBe("Repeated within a list");
  });

  it("never uses validation verdict words that a comparison has not established", () => {
    const banned = /\b(passed|failed|warning|pending|valid|invalid|added|removed)\b/i;
    for (const o of ["only_a", "only_b", "both", "conflict", "duplicate"] as const) {
      expect(outcomeLabel(o, "First list", "Second list")).not.toMatch(banned);
    }
  });
});

describe("comparison of two identifier lists", () => {
  const a = sideFromText("feed", "CSQU3054383\nMSKU1234565\nMSKU1234565\nAPLU9192819\nTCLU4567897");
  const b = sideFromText("master", "CSQU3054383\nMSKU1234565\nAPLU9192812\nCBHU6818017");

  it("classifies added, removed, shared and repeated identifiers without merging anything", () => {
    const res = compare(a, b, "normalized");
    const by = Object.fromEntries(res.entries.map((e) => [e.key, e]));
    expect(by["CSQU3054383"]?.outcome).toBe("both");
    expect(by["MSKU1234565"]?.outcome).toBe("both");
    expect(by["MSKU1234565"]?.note).toContain("count differs: 2 in feed, 1 in master");
    expect(by["TCLU4567897"]?.outcome).toBe("only_a");
    expect(by["CBHU6818017"]?.outcome).toBe("only_b");
    expect(by["APLU9192819"]?.outcome).toBe("only_a");
    expect(by["APLU9192812"]?.outcome).toBe("only_b");
    expect(res.counts).toMatchObject({ only_a: 2, only_b: 2, both: 2, conflict: 0, duplicate: 1 });
  });

  it("comparing on the body reports a check-digit conflict instead of silently matching", () => {
    const res = compare(a, b, "body");
    const e = res.entries.find((x) => x.key === "APLU919281");
    expect(e?.outcome).toBe("conflict");
    expect(e?.note).toContain("APLU9192819 / APLU9192812");
    expect(res.counts["conflict"]).toBe(1);
  });

  it("keeps raw values beside comparable values in the export", () => {
    const x = sideFromText("A", "msku 123456-5");
    const y = sideFromText("B", "MSKU1234565");
    const res = compare(x, y);
    expect(res.entries[0]?.outcome).toBe("both");
    const csv = compareCsv(res, "A", "B");
    expect(csv).toContain("MSKU1234565,both,1,1,msku 123456-5,MSKU1234565");
    expect(csv.startsWith("﻿")).toBe(true);
  });

  it("empty sides produce no entries", () => {
    expect(compare(sideFromText("A", ""), sideFromText("B", "")).entries).toEqual([]);
  });
});
