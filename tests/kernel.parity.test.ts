import { describe, expect, it } from "vitest";

import {
  CheckDigitValueError,
  correctIdentifier,
  correctX12Equipment,
  explain,
  FieldContext,
  IdentifierType,
  iso6346CheckDigit,
  Status,
  uicCheckDigit,
} from "../src/lib/checkdigit";
import * as kernel from "../src/lib/checkdigit";
import {
  loadVectors,
  policiesFrom,
  runCalcVectors,
  runDecisionCases,
  type CalcVector,
  type DecisionCase,
  type DecisionVectors,
} from "./helpers/vectors";

// ---------------------------------------------------------------------------
// test_equipment_checkdigit.py, ported case for case
// ---------------------------------------------------------------------------

describe("algorithm level", () => {
  it("test_iso6346_canonical_example", () => {
    expect(iso6346CheckDigit("CSQU305438")).toBe(3);
  });

  it("test_iso6346_remainder_10_maps_to_zero", () => {
    expect(iso6346CheckDigit("APLU100000")).toBe(0);
  });

  it("test_uic_worked_example", () => {
    expect(uicCheckDigit("21812471217")).toBe(3);
  });

  it("test_iso6346_value_rejects_bad_char", () => {
    expect(() => iso6346CheckDigit("@PLU123456")).toThrow(CheckDigitValueError);
  });
});

describe("SNX fixture: the four containers in 4Containers_snx_Example.xml", () => {
  it("test_snx_fixture", () => {
    const cases: Record<string, [string | null, Status]> = {
      APLU9192812: ["APLU9192819", Status.CORRECTED],
      APLU7979534: ["APLU7979533", Status.CORRECTED],
      APLU2127329: ["APLU2127323", Status.CORRECTED],
      CBHU2426018: [null, Status.VALID],
    };
    for (const [eqid, [corrected, status]] of Object.entries(cases)) {
      const r = correctIdentifier(eqid, FieldContext.EQUIPMENT_ID);
      expect(r.id_type, eqid).toBe(IdentifierType.ISO6346);
      expect(r.status, `${eqid} ${r.reason}`).toBe(status);
      expect(r.corrected, eqid).toBe(corrected);
    }
  });

  it("test_snx_valid_one_is_not_changed", () => {
    const r = correctIdentifier("CBHU2426018", FieldContext.EQUIPMENT_ID);
    expect(r.changed).toBe(false);
    expect(r.corrected).toBeNull();
  });
});

describe("fail-loud confidence gates", () => {
  it("test_freetext_checkfail_is_flagged_not_corrected", () => {
    const r = correctIdentifier("APLU9192812", FieldContext.FREE_TEXT);
    expect(r.status).toBe(Status.FLAGGED);
    expect(r.corrected).toBeNull();
    expect(r.computed_check).toBe("9");
  });

  it("test_freetext_corroborated_by_owner_registry_is_corrected", () => {
    const r = correctIdentifier("APLU9192812", FieldContext.FREE_TEXT, {
      known_owner_prefixes: new Set(["APL"]),
    });
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.corrected).toBe("APLU9192819");
  });

  it("test_unusual_category_is_flagged", () => {
    const r = correctIdentifier("APLX1234560", FieldContext.EQUIPMENT_ID);
    expect(r.status).toBe(Status.FLAGGED);
    expect(r.id_type).toBe(IdentifierType.UNKNOWN);
  });

  it("test_ilu_category_routes_and_shares_math", () => {
    const body = "ABCD123456";
    const token = body + String(iso6346CheckDigit(body));
    const r = correctIdentifier(token, FieldContext.EQUIPMENT_ID);
    expect(r.id_type).toBe(IdentifierType.ILU);
    expect(r.status).toBe(Status.VALID);
  });

  it("test_size_type_passthrough", () => {
    const r = correctIdentifier("L5G1", FieldContext.SIZE_TYPE);
    expect(r.status).toBe(Status.NOT_A_TARGET);
    expect(r.id_type).toBe(IdentifierType.SIZE_TYPE);
  });

  it("test_uic_requires_rail_context_to_correct", () => {
    const validUic = "218124712173";
    expect(correctIdentifier(validUic, FieldContext.UNKNOWN).status).toBe(Status.FLAGGED);
    expect(correctIdentifier(validUic, FieldContext.RAIL_VEHICLE).status).toBe(Status.VALID);
  });

  it("test_uic_correction_in_rail_context", () => {
    const r = correctIdentifier("218124712170", FieldContext.RAIL_VEHICLE);
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.corrected).toBe("218124712173");
  });

  it("test_garbage_is_invalid_structure", () => {
    const r = correctIdentifier("HELLO WORLD", FieldContext.UNKNOWN);
    expect(r.status).toBe(Status.INVALID_STRUCTURE);
  });

  it("test_painted_form_is_normalized", () => {
    const r = correctIdentifier("APLU 919281 2", FieldContext.EQUIPMENT_ID);
    expect(r.normalized).toBe("APLU9192812");
    expect(r.corrected).toBe("APLU9192819");
  });
});

describe("X12 split-field adapter", () => {
  it("test_x12_populates_absent_check_digit", () => {
    const r = correctX12Equipment("EMHU", "123456", "");
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.printed_check).toBeNull();
    expect(r.corrected?.slice(0, 10)).toBe("EMHU123456");
  });

  it("test_x12_validates_correct_check_digit", () => {
    const good = String(iso6346CheckDigit("EMHU123456"));
    expect(correctX12Equipment("EMHU", "123456", good).status).toBe(Status.VALID);
  });

  it("test_x12_corrects_wrong_check_digit", () => {
    const good = String(iso6346CheckDigit("EMHU123456"));
    const wrong = String((Number(good) + 1) % 10);
    const r = correctX12Equipment("EMHU", "123456", wrong);
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.computed_check).toBe(good);
  });

  it("test_x12_rejects_bad_initial", () => {
    expect(correctX12Equipment("EMH", "123456", "1").status).toBe(Status.INVALID_STRUCTURE);
  });
});

describe("non-standard owner (carrier pseudo-prefix)", () => {
  it("test_pseudo_prefix_strict_is_flagged", () => {
    const r = correctIdentifier("L01U1150107", FieldContext.EQUIPMENT_ID);
    expect(r.status).toBe(Status.FLAGGED);
    expect(r.corrected).toBeNull();
    expect(r.computed_check).toBe("5");
  });

  it("test_pseudo_prefix_lenient_is_corrected", () => {
    const r = correctIdentifier("L01U1150107", FieldContext.EQUIPMENT_ID, {
      owner_policy: "lenient",
    });
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.corrected).toBe("L01U1150105");
  });

  it("test_standard_owner_unaffected_by_policy", () => {
    for (const policy of ["strict", "lenient"]) {
      const r = correctIdentifier("APLU9192812", FieldContext.EQUIPMENT_ID, {
        owner_policy: policy,
      });
      expect(r.corrected, policy).toBe("APLU9192819");
    }
  });
});

// ---------------------------------------------------------------------------
// Cases the site must cover beyond the Python test file
// ---------------------------------------------------------------------------

describe("required site cases", () => {
  it("MSKU1234567 is a mismatch with computed 5", () => {
    const r = correctIdentifier("MSKU1234567", FieldContext.EQUIPMENT_ID);
    expect(r.status).toBe(Status.CORRECTED);
    expect(r.printed_check).toBe("7");
    expect(r.computed_check).toBe("5");
    expect(r.corrected).toBe("MSKU1234565");
    const e = explain("MSKU1234567");
    expect(e.ok && e.verdict).toBe("mismatch");
    expect(e.ok && e.computed).toBe(5);
  });

  it("lowercase and space-separated tokens normalize to the same verdict", () => {
    const upper = correctIdentifier("MSKU1234567", FieldContext.EQUIPMENT_ID);
    for (const messy of ["msku 123456 7", "msku-123456-7", " MSKU 1234567 "]) {
      const r = correctIdentifier(messy, FieldContext.EQUIPMENT_ID);
      expect(r.raw).toBe(messy);
      expect({ ...r, raw: upper.raw }).toEqual(upper);
    }
  });

  it("an unknown category letter is flagged, never corrected", () => {
    for (const ctx of Object.values(FieldContext)) {
      if (ctx === FieldContext.SIZE_TYPE) continue;
      const r = correctIdentifier("CONT1234560", ctx, { owner_policy: "lenient" });
      expect(r.status, ctx).toBe(Status.FLAGGED);
      expect(r.id_type, ctx).toBe(IdentifierType.UNKNOWN);
      expect(r.corrected, ctx).toBeNull();
      expect(r.computed_check, ctx).toBe("5");
    }
  });

  it("a 12-digit token outside rail context is flagged even when Luhn passes", () => {
    expect(uicCheckDigit("21812471217")).toBe(3);
    for (const ctx of [FieldContext.EQUIPMENT_ID, FieldContext.FREE_TEXT, FieldContext.UNKNOWN]) {
      const r = correctIdentifier("218124712173", ctx, { owner_policy: "lenient" });
      expect(r.status, ctx).toBe(Status.FLAGGED);
      expect(r.id_type, ctx).toBe(IdentifierType.UIC);
      expect(r.reason, ctx).toContain("Luhn check matches");
    }
  });

  it("rejects an unknown owner_policy with the kernel's message", () => {
    expect(() => correctIdentifier("CSQU3054383", FieldContext.UNKNOWN, { owner_policy: "loose" }))
      .toThrow("owner_policy must be one of ['lenient', 'strict'], got 'loose'");
  });
});

// ---------------------------------------------------------------------------
// run_pass11.py: explain() assertions and the kernel-generated vectors
// ---------------------------------------------------------------------------

describe("explain() assertions from run_pass11.py", () => {
  it("test_explain_iso", () => {
    let r = explain("CSQU3054383");
    if (!r.ok || r.kind !== "iso6346_ilu") throw new Error("expected iso6346_ilu");
    expect(r.verdict).toBe("valid");
    expect(r.computed).toBe(3);
    expect(r.chars).toHaveLength(10);
    expect(r.chars[0]).toEqual({ char: "C", value: 13, weight: 1, product: 13 });
    expect(r.chars[3]?.value).toBe(32);

    r = explain("csqu 3054 38");
    if (!r.ok) throw new Error("expected ok");
    expect(r.verdict).toBe("computed");
    expect(r.full).toBe("CSQU3054383");

    r = explain("APLU1000000");
    if (!r.ok || r.kind !== "iso6346_ilu") throw new Error("expected iso6346_ilu");
    expect(r.remainder_ten).toBe(true);
    expect(r.mod).toBe(10);
    expect(r.computed).toBe(0);
    expect(r.verdict).toBe("valid");

    r = explain("APLU9192812");
    if (!r.ok) throw new Error("expected ok");
    expect(r.verdict).toBe("mismatch");
    expect(r.computed).toBe(9);
    expect(r.full).toBe("APLU9192819");
    expect(r.computed).toBe(iso6346CheckDigit("APLU919281"));

    const cat = (t: string) => {
      const e = explain(t);
      return e.ok && e.kind === "iso6346_ilu" ? e.category_set : null;
    };
    expect(cat("MSCK1234560")).toBe("ilu");
    expect(cat("CONT1234565")).toBe("unknown");
    expect(cat("MSKU1234561")).toBe("iso6346");
  });

  it("test_explain_uic_and_errors", () => {
    const r = explain("218124712173");
    if (!r.ok || r.kind !== "uic") throw new Error("expected uic");
    expect(r.verdict).toBe("valid");
    expect(r.computed).toBe(3);
    expect(r.steps.reduce((acc, s) => acc + s.product, 0)).toBe(r.sum);

    const r2 = explain("21 81 2471 217");
    if (!r2.ok) throw new Error("expected ok");
    expect(r2.verdict).toBe("computed");
    expect(r2.full).toBe("218124712173");

    for (const bad of ["", "MSKU12345", "ABCDE123456", "12345", "MSK U123456789"]) {
      expect(explain(bad).ok, bad).toBe(false);
    }
  });
});

describe("kernel-truth vectors (run_pass11.write_js_vectors)", () => {
  const vectors = loadVectors<CalcVector[]>("calc_vectors.json");

  it("has the 11 kernel cases", () => {
    expect(vectors).toHaveLength(11);
  });

  for (const v of vectors) {
    it(`explain(${JSON.stringify(v.input)})`, () => {
      runCalcVectors(kernel, [v]);
    });
  }
});

// ---------------------------------------------------------------------------
// Decision-table vectors: every field of every CorrectionResult, reason
// text included, generated by scripts/gen_decision_vectors.py
// ---------------------------------------------------------------------------

describe("decision-table vectors (scripts/gen_decision_vectors.py)", () => {
  const data = loadVectors<DecisionVectors>("decision_vectors.json");
  const policies = policiesFrom(data);

  it("covers the full grid", () => {
    expect(data.cases.length).toBeGreaterThan(500);
    const fns = new Set(data.cases.map((c) => c.fn));
    expect(fns).toEqual(new Set(["correct_identifier", "correct_x12_equipment"]));
    const contexts = new Set(data.cases.map((c) => c.context).filter(Boolean));
    expect(contexts).toEqual(new Set(Object.values(FieldContext)));
  });

  const byPolicy = new Map<string, DecisionCase[]>();
  for (const c of data.cases) {
    const key = `${c.fn} / policy=${c.policy}`;
    byPolicy.set(key, [...(byPolicy.get(key) ?? []), c]);
  }

  for (const [group, cases] of byPolicy) {
    it(`${group} (${cases.length} cases)`, () => {
      runDecisionCases(kernel, cases, policies);
    });
  }

  it("raises the kernel's ValueError messages", () => {
    for (const e of data.errors) {
      const label = `${e.fn}(${e.arg})`;
      switch (e.fn) {
        case "iso6346_value":
          // reached through a body that contains the bad character
          expect(() => iso6346CheckDigit(`${e.arg}PLU123456`.slice(0, 10)), label).toThrow(e.message);
          break;
        case "iso6346_check_digit":
          expect(() => iso6346CheckDigit(e.arg), label).toThrow(e.message);
          break;
        case "uic_check_digit":
          expect(() => uicCheckDigit(e.arg), label).toThrow(e.message);
          break;
        case "correct_identifier":
          expect(
            () => correctIdentifier("CSQU3054383", FieldContext.UNKNOWN, { owner_policy: "loose" }),
            label,
          ).toThrow(e.message);
          break;
        default:
          throw new Error(`no mapping for ${e.fn}`);
      }
    }
  });
});
