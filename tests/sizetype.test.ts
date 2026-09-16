import { describe, expect, it } from "vitest";

import { decodeSizeType, GROUP_COLOR, LENGTH_CODES, VERIFY_NOTES } from "../src/lib/sizetype";
import { loadVectors } from "./helpers/vectors";

interface SizeTypeVectors {
  vectors: { code: string; decoded: Record<string, unknown> }[];
  group_color: Record<string, string>;
}

describe("size/type decoder parity with iso6346_sizetype.py", () => {
  const data = loadVectors<SizeTypeVectors>("sizetype_vectors.json");

  for (const v of data.vectors) {
    it(`decode(${JSON.stringify(v.code)})`, () => {
      expect(decodeSizeType(v.code)).toEqual(v.decoded);
    });
  }

  it("keeps the group palette and omits length code 5 deliberately", () => {
    expect(GROUP_COLOR).toEqual(data.group_color);
    expect(LENGTH_CODES).not.toContain("5");
    expect(decodeSizeType("55G1").defined).toBe(false);
    expect(decodeSizeType("55G1").notes[0]).toContain("'5' unassigned");
    expect(VERIFY_NOTES).toHaveLength(2);
  });
});
