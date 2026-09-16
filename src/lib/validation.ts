import {
  correctIdentifier,
  explain,
  FieldContext,
  normalize,
  Status,
  type CorrectionResult,
  type Explain,
} from "./checkdigit";

/*
 * Layered validation for one identifier. Each layer reports its own state,
 * source and rule version so no single badge can stand for all of them. The
 * public checker can only establish structure and check-digit agreement; every
 * other layer is reported as not checked (with the reason) rather than implied.
 */

export const LAYERS = [
  "structure",
  "check_digit",
  "prefix_registration",
  "equipment_record",
  "attribute_consistency",
  "operational_state",
  "message_profile_acceptance",
] as const;
export type Layer = (typeof LAYERS)[number];

export type LayerState =
  | "passed"
  | "failed"
  | "warning"
  | "not_checked"
  | "unavailable"
  | "stale"
  | "unsupported"
  | "insufficient_information";

export interface LayerResult {
  layer: Layer;
  state: LayerState;
  source: string;
  rule_version: string;
  checked_at: string | null;
  detail: string;
}

export type SchemeChoice = "auto" | "iso6346" | "ilu" | "uic";

export interface TokenValidation {
  raw: string;
  normalized: string;
  scheme: "iso6346" | "ilu" | "uic" | "unknown";
  layers: LayerResult[];
  kernel: CorrectionResult | null;
  explanation: Explain | null;
}

export const RULE_VERSIONS = {
  iso6346: "iso6346-mod11/1",
  ilu: "ilu-en13044-mod11/1 (equivalence to ISO 6346 unconfirmed against the normative text)",
  uic: "uic-luhn-mod10/1",
} as const;

const NOT_CHECKED: Record<Exclude<Layer, "structure" | "check_digit">, string> = {
  prefix_registration: "No prefix register is loaded in the public checker. A registered prefix would not prove this serial number is correct.",
  equipment_record: "No equipment source is queried in the public checker. A missing record would not prove the unit does not exist.",
  attribute_consistency: "No size/type, mass or owner attributes were supplied with this number.",
  operational_state: "No terminal or carrier is consulted. Nothing here proves a booking, hold, release, location or permission to release.",
  message_profile_acceptance: "A single number is not a message; syntax, schema and partner rules apply to files.",
};

function layer(l: Layer, state: LayerState, source: string, rule_version: string, detail: string, checked: boolean): LayerResult {
  return { layer: l, state, source, rule_version, checked_at: checked ? new Date().toISOString() : null, detail };
}

function schemeOf(e: Explain, kernel: CorrectionResult | null): TokenValidation["scheme"] {
  if (e.ok && e.kind === "uic") return "uic";
  if (e.ok && e.kind === "iso6346_ilu") {
    if (e.category_set === "iso6346") return "iso6346";
    if (e.category_set === "ilu") return "ilu";
    return "unknown";
  }
  if (kernel && kernel.id_type === "uic") return "uic";
  return "unknown";
}

export function validateToken(raw: string, choice: SchemeChoice = "auto"): TokenValidation {
  const normalized = normalize(raw);
  const e = explain(raw);
  const kernel = normalized ? correctIdentifier(raw, FieldContext.EQUIPMENT_ID) : null;
  const scheme = schemeOf(e, kernel);
  const layers: LayerResult[] = [];
  const rule = scheme === "uic" ? RULE_VERSIONS.uic : scheme === "ilu" ? RULE_VERSIONS.ilu : RULE_VERSIONS.iso6346;

  // Explicit scheme selection: a mismatch between what was chosen and what the
  // shape implies is a structure failure, never a silent reroute.
  if (choice !== "auto" && e.ok) {
    const implied = e.kind === "uic" ? "uic" : scheme === "unknown" ? "unknown" : scheme;
    if (implied !== choice && !(choice !== "uic" && implied !== "uic" && implied === "unknown")) {
      layers.push(layer("structure", "failed", "kernel", rule,
        `Selected scheme ${choice} but the number has the shape of ${implied === "unknown" ? "an unknown category" : implied}.`, true));
      layers.push(layer("check_digit", "not_checked", "kernel", rule, "Not judged under a mismatched scheme.", false));
      for (const l of LAYERS.slice(2)) layers.push(layer(l, "not_checked", "none", "n/a", NOT_CHECKED[l as keyof typeof NOT_CHECKED], false));
      return { raw, normalized, scheme, layers, kernel, explanation: e };
    }
  }

  if (!e.ok) {
    layers.push(layer("structure", "failed", "kernel", rule, e.error, true));
    layers.push(layer("check_digit", "not_checked", "kernel", rule, "No recognized shape to compute from.", false));
  } else if (e.kind === "iso6346_ilu" && scheme === "unknown") {
    layers.push(layer("structure", "warning", "kernel", rule,
      `Category letter ${e.category} is neither ISO 6346 (U, J, Z) nor ILU (A, B, D, E, K); the shape matches but the scheme is unknown.`, true));
    pushCheck(layers, e, rule);
  } else {
    const shape = e.kind === "uic" ? "11 or 12 digits (UIC wagon)" : "4 letters, 6 digits, 1 check digit";
    const catNote = e.kind === "iso6346_ilu"
      ? e.category === "U" ? " Category U: freight container." : e.category === "J" ? " Category J: detachable freight-container-related equipment." : e.category === "Z" ? " Category Z: trailer or chassis." : ` Category ${e.category}: ILU (EN 13044).`
      : "";
    layers.push(layer("structure", "passed", "kernel", rule, `Shape matches ${shape}.${catNote}`, true));
    pushCheck(layers, e, rule);
  }
  for (const l of LAYERS.slice(2)) {
    layers.push(layer(l, "not_checked", "none", "n/a", NOT_CHECKED[l as keyof typeof NOT_CHECKED], false));
  }
  return { raw, normalized, scheme, layers, kernel, explanation: e };
}

function pushCheck(layers: LayerResult[], e: Explain, rule: string): void {
  if (!e.ok) return;
  if (e.printed === null) {
    layers.push(layer("check_digit", "insufficient_information", "kernel", rule,
      `No check digit supplied. Expected check digit for this body: ${e.computed}.`, true));
  } else if (e.verdict === "valid") {
    layers.push(layer("check_digit", "passed", "kernel", rule, `Printed ${e.printed} agrees with computed ${e.computed}.`, true));
  } else {
    layers.push(layer("check_digit", "failed", "kernel", rule,
      `Printed ${e.printed}, expected ${e.computed} for this body. Either the digit or the body is wrong.`, true));
  }
}

export const isBlockedByKernel = (v: TokenValidation): boolean =>
  v.kernel !== null && (v.kernel.status === Status.FLAGGED || v.kernel.status === Status.INVALID_STRUCTURE);
