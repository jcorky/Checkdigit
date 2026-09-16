import { describe, expect, it } from "vitest";

import { LAYERS, validateToken } from "../src/lib/validation";

const state = (token: string, layer: string, choice: "auto" | "iso6346" | "ilu" | "uic" = "auto") =>
  validateToken(token, choice).layers.find((l) => l.layer === layer)?.state;

describe("layered validation never collapses into one badge", () => {
  it("reports every layer with a source and rule version", () => {
    const v = validateToken("CSQU3054383");
    expect(v.layers.map((l) => l.layer)).toEqual([...LAYERS]);
    for (const l of v.layers) {
      expect(l.source.length).toBeGreaterThan(0);
      expect(l.rule_version.length).toBeGreaterThan(0);
      expect(l.detail.length).toBeGreaterThan(0);
    }
  });

  it("a correct check digit passes structure and check digit only", () => {
    expect(state("CSQU3054383", "structure")).toBe("passed");
    expect(state("CSQU3054383", "check_digit")).toBe("passed");
    for (const layer of ["prefix_registration", "equipment_record", "operational_state", "message_profile_acceptance"]) {
      expect(state("CSQU3054383", layer)).toBe("not_checked");
    }
    expect(state("CSQU3054383", "attribute_consistency")).toBe("not_checked");
    const v = validateToken("CSQU3054383");
    expect(v.layers.find((l) => l.layer === "operational_state")?.detail).toContain("permission to release");
  });

  it("a wrong check digit fails the check-digit layer and names the expected digit", () => {
    const v = validateToken("MSKU1234567");
    expect(state("MSKU1234567", "structure")).toBe("passed");
    const check = v.layers.find((l) => l.layer === "check_digit");
    expect(check?.state).toBe("failed");
    expect(check?.detail).toContain("expected 5");
    expect(v.kernel?.status).toBe("corrected");
  });

  it("a missing check digit is insufficient information, not a failure", () => {
    const v = validateToken("msku 123456");
    expect(v.normalized).toBe("MSKU123456");
    expect(state("msku 123456", "check_digit")).toBe("insufficient_information");
    expect(v.layers.find((l) => l.layer === "check_digit")?.detail).toContain("Expected check digit for this body: 5");
  });

  it("an unknown category letter is a structure warning with the scheme unknown", () => {
    const v = validateToken("APLX1234560");
    expect(v.scheme).toBe("unknown");
    expect(state("APLX1234560", "structure")).toBe("warning");
    expect(state("APLX1234560", "check_digit")).toBe("failed");
    expect(v.kernel?.status).toBe("flagged");
  });

  it("UIC wagons use the Luhn rule and stay separate from containers", () => {
    const v = validateToken("21 81 2471 217-3");
    expect(v.scheme).toBe("uic");
    expect(state("21 81 2471 217-3", "check_digit")).toBe("passed");
    expect(v.layers[0]?.rule_version).toContain("luhn");
  });

  it("an explicit scheme that contradicts the shape is a structure failure, not a reroute", () => {
    expect(state("218124712173", "structure", "iso6346")).toBe("failed");
    expect(state("218124712173", "check_digit", "iso6346")).toBe("not_checked");
    expect(state("CSQU3054383", "structure", "uic")).toBe("failed");
    expect(state("CSQU3054383", "structure", "iso6346")).toBe("passed");
    expect(state("ABCD1234566", "structure", "ilu")).toBe("passed");
    expect(state("CSQU3054383", "structure", "ilu")).toBe("failed");
  });

  it("garbage fails structure and checks nothing else", () => {
    const v = validateToken("HELLO WORLD");
    expect(state("HELLO WORLD", "structure")).toBe("failed");
    expect(state("HELLO WORLD", "check_digit")).toBe("not_checked");
    expect(v.kernel?.status).toBe("invalid_structure");
  });
});
