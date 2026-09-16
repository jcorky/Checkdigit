import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { FieldContext, IdentifierType, Status } from "../src/lib/checkdigit";
import { ENUMS, FINDINGS, finding, isEnumValue } from "../src/lib/contracts";
import { LAYERS, validateToken } from "../src/lib/validation";
import { validate, type SchemaDoc } from "./helpers/schema";

const contractsDir = resolve(import.meta.dirname, "..", "contracts");
const schema = JSON.parse(readFileSync(resolve(contractsDir, "entities.schema.json"), "utf8")) as SchemaDoc;
const examples = JSON.parse(readFileSync(resolve(contractsDir, "examples.json"), "utf8")) as {
  valid: Record<string, unknown>;
  invalid: Record<string, { entity: string; document: unknown }>;
};

describe("shared enumerations agree with the kernel", () => {
  it("identifier schemes, field contexts and statuses are the kernel's values", () => {
    expect([...ENUMS.identifier_scheme].sort()).toEqual(Object.values(IdentifierType).sort());
    expect([...ENUMS.field_context].sort()).toEqual(Object.values(FieldContext).sort());
    expect([...ENUMS.kernel_status].sort()).toEqual(Object.values(Status).sort());
  });

  it("validation layers and states match the layered validator", () => {
    expect([...LAYERS]).toEqual(ENUMS.validation_layer);
    for (const layer of validateToken("CSQU3054383").layers) {
      expect(isEnumValue("validation_state", layer.state), layer.state).toBe(true);
    }
  });

  it("every x-enum in the schema names an enumeration", () => {
    const text = JSON.stringify(schema);
    const names = new Set([...text.matchAll(/"x-enum":"([a-z_]+)"/g)].map((m) => m[1] as string));
    expect(names.size).toBeGreaterThan(10);
    for (const name of names) expect(Array.isArray((ENUMS as Record<string, unknown>)[name]), name).toBe(true);
  });
});

describe("finding codes", () => {
  it("cover the required workflow outcomes", () => {
    for (const code of [
      "IMPORT_INTENT_UNRESOLVED",
      "SNAPSHOT_INCOMPLETE",
      "BASELINE_CONFLICT",
      "MAPPING_DRIFT",
      "MESSAGE_ID_CONFLICT",
      "PREDECESSOR_UNRESOLVED",
      "CONTEXT_AMBIGUOUS",
      "APPROVAL_STALE",
      "FEEDBACK_UNMATCHED",
      "FEEDBACK_AMBIGUOUS",
      "DELIVERY_OUTCOME_UNKNOWN",
    ]) {
      const f = finding(code);
      expect(f.explanation.length).toBeGreaterThan(20);
      expect(f.recovery.length).toBeGreaterThan(5);
      expect(["info", "warning", "blocking"]).toContain(f.severity);
    }
    expect(new Set(FINDINGS.map((f) => f.code)).size).toBe(FINDINGS.length);
    expect(() => finding("NOT_A_CODE")).toThrow();
  });
});

describe("entity examples validate against the schema", () => {
  for (const [name, doc] of Object.entries(examples.valid)) {
    it(`${name} example is valid`, () => {
      const def = schema.$defs[name];
      expect(def, `schema has no $def ${name}`).toBeDefined();
      expect(validate(schema, def as Record<string, unknown>, doc, ENUMS)).toEqual([]);
    });
  }

  it("every entity in the schema has an example", () => {
    const withExample = new Set(Object.keys(examples.valid));
    const helpers = new Set(["id", "sha256", "timestamp", "version", "mapping_contract", "coverage_scope", "source_location", "identifier_assertion", "layer_result", "event_time", "edit", "selection_manifest"]);
    for (const name of Object.keys(schema.$defs)) {
      if (!helpers.has(name)) expect(withExample.has(name), name).toBe(true);
    }
  });

  for (const [name, { entity, document }] of Object.entries(examples.invalid)) {
    it(`${name} is rejected`, () => {
      const errors = validate(schema, schema.$defs[entity] as Record<string, unknown>, document, ENUMS);
      expect(errors.length).toBeGreaterThan(0);
    });
  }
});
