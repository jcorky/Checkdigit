import { describe, expect, it } from "vitest";

import { checkMapping, fingerprint, proposeContract, type MappingContract } from "../src/lib/mapping";

const named: MappingContract = {
  version: 1,
  positional: false,
  delimiter: ",",
  has_header: true,
  column_count: null,
  fields: [
    { name: "container", identity: "container", required: true, type: "identifier" },
    { name: "ref", identity: "ref", required: false, type: "text" },
  ],
};

const positional: MappingContract = {
  version: 1,
  positional: true,
  delimiter: ",",
  has_header: false,
  column_count: 3,
  fields: [{ name: "container", identity: 1, required: true, type: "identifier" }],
};

describe("feed layout changes against the mapping contract", () => {
  it("named columns reordered still map by identity and the reorder is recorded", () => {
    const text = "ref,container\nA1,CSQU3054384\nA2,MSKU1234565\n";
    const check = checkMapping(text, named);
    expect(check.blocked).toBe(false);
    expect(check.resolved).toEqual([{ field: "container", column: 1 }, { field: "ref", column: 0 }]);
    expect(check.findings.map((f) => f.code)).toEqual(["MAPPING_REORDER_HARMLESS"]);
    expect(check.rows).toBe(2);
  });

  it("a positional feed with a changed column count is blocked", () => {
    const check = checkMapping("A1,CSQU3054384\nA2,MSKU1234565\n", positional);
    expect(check.blocked).toBe(true);
    expect(check.findings[0]?.code).toBe("MAPPING_DRIFT");
    expect(check.findings[0]?.blocking_scope).toBe("feed_transformation");
    expect(check.resolved[0]?.column).toBeNull();
  });

  it("duplicate headers cannot silently pick the first match", () => {
    const check = checkMapping("container,container\nCSQU3054384,MSKU1234565\n", named);
    expect(check.blocked).toBe(true);
    expect(check.findings.some((f) => f.code === "MAPPING_DRIFT" && f.detail.includes("Duplicate header"))).toBe(true);
  });

  it("a renamed required column blocks with the observed header listed", () => {
    const check = checkMapping("ref,box\nA1,CSQU3054384\n", named);
    expect(check.blocked).toBe(true);
    expect(check.findings[0]?.detail).toContain('Required column "container" is missing');
    expect(check.findings[0]?.detail).toContain('"box"');
  });

  it("new optional columns are preserved, not discarded", () => {
    const check = checkMapping("container,ref,vgm\nCSQU3054384,A1,12000\n", named);
    expect(check.blocked).toBe(false);
    expect(check.findings.map((f) => f.code)).toEqual(["MAPPING_EXTENSION_PRESERVED"]);
  });

  it("an unexpected value late in the stream prevents a completeness claim", () => {
    const good = Array.from({ length: 20 }, (_, i) => `A${i},MSKU${String(100000 + i).padStart(6, "0")}0`).join("\n");
    const late = Array.from({ length: 8 }, (_, i) => `B${i},2026-09-1${i % 10} 08:00`).join("\n");
    const check = checkMapping(`ref,container\n${good}\n${late}\n`, named);
    expect(check.blocked).toBe(true);
    const f = check.findings.find((x) => x.code === "MAPPING_VIOLATION_LATE");
    expect(f?.detail).toContain("8 of 28 rows");
    expect(f?.location).toBe("row 21");
    expect(check.violations[0]?.row).toBe(21);
  });

  it("an isolated invalid record is reported without blocking the feed", () => {
    const check = checkMapping("ref,container\nA1,CSQU3054384\nA2,not-a-number\nA3,MSKU1234565\n", named);
    expect(check.blocked).toBe(false);
    const f = check.findings.filter((x) => x.code === "MAPPING_VIOLATION_LATE");
    expect(f).toHaveLength(1);
    expect(f[0]?.location).toBe("row 2");
  });

  it("the observed fingerprint is structural, not a file hash", () => {
    const a = fingerprint("container,ref\nCSQU3054384,A1\n", named);
    const b = fingerprint("container,ref\nMSKU1234565,B2\nAPLU9192812,C3\n", named);
    expect(a.header).toEqual(b.header);
    expect(a.column_count).toBe(b.column_count);
    expect(a.rows).toBe(1);
    expect(b.rows).toBe(2);
  });

  it("proposes a contract from a header but marks nothing authoritative", () => {
    const c = proposeContract("ref,container,remark\nA1,CSQU3054384,x\nA2,MSKU1234565,y\n");
    expect(c.has_header).toBe(true);
    expect(c.positional).toBe(false);
    expect(c.fields.map((f) => [f.name, f.type, f.required])).toEqual([["ref", "text", false], ["container", "identifier", true], ["remark", "text", false]]);
    const p = proposeContract("A1,CSQU3054384\nA2,MSKU1234565\n");
    expect(p.positional).toBe(true);
    expect(p.fields[1]?.type).toBe("identifier");
  });
});
