/*
 * Check-digit kernel for shipping and terminal equipment identifiers.
 *
 * This is a line-for-line port of checkdigit/equipment_checkdigit.py. Each
 * function names the Python lines it mirrors. Result objects keep the Python
 * field names (printed_check, computed_check, id_type ...) so the parity
 * vectors and the report shape compare directly.
 *
 * ISO 6346 letter values run A=10..Z=38 skipping multiples of 11; weights are
 * 2^0..2^9 over the ten leading characters; remainder 10 maps to 0.
 * EN 13044 (ILU) reuses the same arithmetic with category letters A/B/D/E/K.
 * That equivalence is not yet confirmed against the normative EN 13044-1 text
 * (equipment_checkdigit.py:32-33); only the label differs.
 * UIC 12-digit wagon numbers use Luhn mod 10 over the first eleven digits.
 */

/* CALC-PURE-BEGIN */

// equipment_checkdigit.py:51-60
//   [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24,
//    25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38]
const LETTER_VALUE_LIST = [
  10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24,
  25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38,
] as const;

export const ISO6346_LETTER_VALUES: Readonly<Record<string, number>> = Object.freeze(
  Object.fromEntries(
    Array.from("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (ch, i) => [ch, LETTER_VALUE_LIST[i] as number]),
  ),
);

/** Raised where the Python kernel raises ValueError. */
export class CheckDigitValueError extends Error {
  override name = "CheckDigitValueError";
}

/** Python repr() of a string, used so error and reason text matches the kernel. */
function repr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  const escaped = s
    .replace(/\\/g, "\\\\")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t")
    .replace(new RegExp(quote, "g"), `\\${quote}`);
  return quote + escaped + quote;
}

const isAsciiDigit = (ch: string): boolean => ch.length === 1 && ch >= "0" && ch <= "9";
const isAllAsciiDigits = (s: string): boolean => /^[0-9]+$/.test(s);
const isAllAsciiAlpha = (s: string): boolean => /^[A-Za-z]+$/.test(s);

// equipment_checkdigit.py:63-70
//   if ch.isdigit(): return int(ch)
//   raise ValueError(f"Not a valid ISO 6346 character: {ch!r}")
export function iso6346Value(ch: string): number {
  if (isAsciiDigit(ch)) return Number(ch);
  const value = ISO6346_LETTER_VALUES[ch];
  if (value === undefined) {
    throw new CheckDigitValueError(`Not a valid ISO 6346 character: ${repr(ch)}`);
  }
  return value;
}

// equipment_checkdigit.py:73-92
//   total += iso6346_value(ch) * (1 << i)
//   remainder = total % 11
//   return 0 if remainder == 10 else remainder
export function iso6346CheckDigit(body10: string): number {
  if (body10.length !== 10) {
    throw new CheckDigitValueError(
      `ISO 6346 body must be 10 chars, got ${body10.length}: ${repr(body10)}`,
    );
  }
  let total = 0;
  for (let i = 0; i < 10; i++) {
    total += iso6346Value(body10[i] as string) * (1 << i);
  }
  const remainder = total % 11;
  return remainder === 10 ? 0 : remainder;
}

// equipment_checkdigit.py:95-114
//   for i, ch in enumerate(reversed(body11)):
//       product = int(ch) * (2 if i % 2 == 0 else 1)
//       if product >= 10: product = product // 10 + product % 10
//   return (10 - (total % 10)) % 10
export function uicCheckDigit(body11: string): number {
  if (body11.length !== 11 || !isAllAsciiDigits(body11)) {
    throw new CheckDigitValueError(`UIC body must be 11 digits, got ${repr(body11)}`);
  }
  let total = 0;
  for (let i = 0; i < 11; i++) {
    const ch = body11[10 - i] as string;
    let product = Number(ch) * (i % 2 === 0 ? 2 : 1);
    if (product >= 10) product = Math.floor(product / 10) + (product % 10);
    total += product;
  }
  return (10 - (total % 10)) % 10;
}

// equipment_checkdigit.py:121-154
export const IdentifierType = {
  ISO6346: "iso6346",
  ILU: "ilu",
  UIC: "uic",
  SIZE_TYPE: "size_type",
  UNKNOWN: "unknown",
} as const;
export type IdentifierType = (typeof IdentifierType)[keyof typeof IdentifierType];

export const FieldContext = {
  EQUIPMENT_ID: "equipment_id",
  RAIL_VEHICLE: "rail_vehicle",
  SIZE_TYPE: "size_type",
  FREE_TEXT: "free_text",
  UNKNOWN: "unknown",
} as const;
export type FieldContext = (typeof FieldContext)[keyof typeof FieldContext];

export const Status = {
  VALID: "valid",
  CORRECTED: "corrected",
  FLAGGED: "flagged",
  NOT_A_TARGET: "not_a_target",
  INVALID_STRUCTURE: "invalid_structure",
} as const;
export type Status = (typeof Status)[keyof typeof Status];

export type Confidence = "high" | "low";
export type OwnerPolicy = "strict" | "lenient";

// equipment_checkdigit.py:157-171
export interface CorrectionResult {
  raw: string;
  normalized: string;
  id_type: IdentifierType;
  status: Status;
  confidence: Confidence;
  reason: string;
  printed_check: string | null;
  computed_check: string | null;
  corrected: string | null;
  changed: boolean;
}

function result(
  fields: Omit<CorrectionResult, "printed_check" | "computed_check" | "corrected" | "changed"> &
    Partial<Pick<CorrectionResult, "printed_check" | "computed_check" | "corrected">>,
): CorrectionResult {
  return {
    raw: fields.raw,
    normalized: fields.normalized,
    id_type: fields.id_type,
    status: fields.status,
    confidence: fields.confidence,
    reason: fields.reason,
    printed_check: fields.printed_check ?? null,
    computed_check: fields.computed_check ?? null,
    corrected: fields.corrected ?? null,
    changed: fields.status === Status.CORRECTED,
  };
}

// equipment_checkdigit.py:176-181
export const RE_BIC_LIKE = /^[A-Z]{3}([A-Z])[0-9]{6}([0-9])$/;
export const RE_CONTAINER_SHAPED = /^[A-Z0-9]{4}[0-9]{7}$/;
export const RE_UIC = /^[0-9]{12}$/;
export const ISO6346_CATEGORIES: ReadonlySet<string> = new Set("UJZ");
export const ILU_CATEGORIES: ReadonlySet<string> = new Set("ABDEK");
export const OWNER_POLICIES: ReadonlySet<string> = new Set(["strict", "lenient"]);

/** The operator policy contract (policy.py: Policy.resolve -> ResolvedTreatment). */
export interface ResolvedTreatment {
  owner_policy: OwnerPolicy;
  force_flag: boolean;
  corroborated: boolean;
}
export interface PolicyLike {
  resolve(prefix: string): ResolvedTreatment;
}

export interface CorrectOptions {
  known_owner_prefixes?: ReadonlySet<string> | null;
  owner_policy?: string;
  policy?: PolicyLike | null;
}

// equipment_checkdigit.py:188-194
//   return re.sub(r"[\s\-]", "", token).upper()
export function normalize(token: string): string {
  return token.replace(/[\s-]/g, "").toUpperCase();
}

const label = (idType: IdentifierType): string => idType.toUpperCase();

// equipment_checkdigit.py:197-208
export function evaluateBic(
  body10: string,
): { id_type: IdentifierType; computed: string; nonstandard: boolean } {
  const category = body10[3] as string;
  const computed = String(iso6346CheckDigit(body10));
  if (ISO6346_CATEGORIES.has(category)) {
    return { id_type: IdentifierType.ISO6346, computed, nonstandard: false };
  }
  if (ILU_CATEGORIES.has(category)) {
    return { id_type: IdentifierType.ILU, computed, nonstandard: false };
  }
  return { id_type: IdentifierType.UNKNOWN, computed, nonstandard: true };
}

const ownerPolicyError = (value: string): CheckDigitValueError =>
  new CheckDigitValueError(
    `owner_policy must be one of ['lenient', 'strict'], got ${repr(value)}`,
  );

// equipment_checkdigit.py:211-336
export function correctIdentifier(
  token: string,
  context: FieldContext = FieldContext.UNKNOWN,
  options: CorrectOptions = {},
): CorrectionResult {
  let knownOwnerPrefixes = options.known_owner_prefixes ?? null;
  let ownerPolicy = options.owner_policy ?? "strict";
  const policy = options.policy ?? null;

  // :238-258 -- operator policy layer, resolved for the token's owner prefix
  if (policy !== null) {
    const prefix = normalize(token).slice(0, 3);
    const t = policy.resolve(prefix);
    ownerPolicy = t.owner_policy;
    if (t.corroborated) {
      knownOwnerPrefixes = new Set([...(knownOwnerPrefixes ?? []), prefix]);
    }
    if (t.force_flag) {
      const base = correctIdentifier(token, context, {
        known_owner_prefixes: knownOwnerPrefixes,
        owner_policy: ownerPolicy,
      });
      if (
        base.status === Status.VALID ||
        base.status === Status.CORRECTED ||
        base.status === Status.FLAGGED
      ) {
        return result({
          raw: base.raw,
          normalized: base.normalized,
          id_type: base.id_type,
          status: Status.FLAGGED,
          confidence: base.confidence,
          reason:
            `Owner prefix ${repr(prefix)} is on the operator deny list ` +
            `(review required, never auto-corrected). ` +
            (base.reason || ""),
          printed_check: base.printed_check,
          computed_check: base.computed_check,
        });
      }
      return base;
    }
  }

  // :260-262
  if (!OWNER_POLICIES.has(ownerPolicy)) throw ownerPolicyError(ownerPolicy);
  const raw = token;
  const norm = normalize(token);

  // :266-272 -- size/type fields are never a correction target
  if (context === FieldContext.SIZE_TYPE) {
    return result({
      raw,
      normalized: norm,
      id_type: IdentifierType.SIZE_TYPE,
      status: Status.NOT_A_TARGET,
      confidence: "high",
      reason: "Field is an ISO 6346 size/type code (descriptive); not a check-digit target.",
    });
  }

  // :274-294 -- 4 letters + 7 digits: ISO 6346 container or EN 13044 ILU
  const m = RE_BIC_LIKE.exec(norm);
  if (m) {
    const category = m[1] as string;
    const printed = m[2] as string;
    const body10 = norm.slice(0, 10);
    const { id_type, computed, nonstandard } = evaluateBic(body10);
    const correctedFull = body10 + computed;

    if (nonstandard) {
      return result({
        raw,
        normalized: norm,
        id_type: IdentifierType.UNKNOWN,
        status: Status.FLAGGED,
        confidence: "low",
        reason:
          `Shape matches a container number but category letter ${repr(category)} ` +
          `is neither ISO 6346 (U/J/Z) nor ILU (A/B/D/E/K). ` +
          `Suggested check digit ${computed}; not auto-applied.`,
        printed_check: printed,
        computed_check: computed,
      });
    }

    return decideBic(raw, norm, id_type, context, printed, computed, correctedFull, knownOwnerPrefixes);
  }

  // :296-324 -- 4 alnum + 7 digits: container-shaped but owner is NOT 3 letters
  if (RE_CONTAINER_SHAPED.test(norm)) {
    const body10 = norm.slice(0, 10);
    const printed = norm[10] as string;
    const owner = norm.slice(0, 3);
    const computed = String(iso6346CheckDigit(body10));
    const nonconformant =
      `Container-shaped but owner code ${repr(owner)} is not three letters ` +
      `(ISO 6346 requires a 3-letter owner) -- likely a synthetic / SOC / ` +
      `carrier pseudo-prefix.`;
    if (
      ownerPolicy === "lenient" &&
      (context === FieldContext.EQUIPMENT_ID || context === FieldContext.UNKNOWN)
    ) {
      if (printed === computed) {
        return result({
          raw,
          normalized: norm,
          id_type: IdentifierType.ISO6346,
          status: Status.VALID,
          confidence: "low",
          reason:
            nonconformant +
            ` Check digit ${printed} is consistent under standard mod-11 (lenient policy).`,
          printed_check: printed,
          computed_check: computed,
        });
      }
      return result({
        raw,
        normalized: norm,
        id_type: IdentifierType.ISO6346,
        status: Status.CORRECTED,
        confidence: "low",
        reason:
          nonconformant +
          ` Corrected ${printed} -> ${computed} under standard mod-11 (lenient policy).`,
        printed_check: printed,
        computed_check: computed,
        corrected: body10 + computed,
      });
    }
    return result({
      raw,
      normalized: norm,
      id_type: IdentifierType.ISO6346,
      status: Status.FLAGGED,
      confidence: "low",
      reason:
        nonconformant +
        ` Suggested check digit ${computed} (standard mod-11); ` +
        `not auto-applied -- confirm prefix policy.`,
      printed_check: printed,
      computed_check: computed,
    });
  }

  // :326-330 -- 12 digits: possible UIC vehicle number
  if (RE_UIC.test(norm)) {
    const body11 = norm.slice(0, 11);
    const printed = norm[11] as string;
    const computed = String(uicCheckDigit(body11));
    return decideUic(raw, norm, context, printed, computed, body11 + computed);
  }

  // :332-336 -- nothing recognized
  return result({
    raw,
    normalized: norm,
    id_type: IdentifierType.UNKNOWN,
    status: Status.INVALID_STRUCTURE,
    confidence: "high",
    reason: "Does not match any known equipment-identifier structure.",
  });
}

// equipment_checkdigit.py:339-384
export function decideBic(
  raw: string,
  norm: string,
  idType: IdentifierType,
  context: FieldContext,
  printed: string,
  computed: string,
  correctedFull: string,
  knownOwnerPrefixes: ReadonlySet<string> | null,
): CorrectionResult {
  const lbl = label(idType);
  const owner = norm.slice(0, 3);

  if (printed === computed) {
    return result({
      raw,
      normalized: norm,
      id_type: idType,
      status: Status.VALID,
      confidence: "high",
      reason: `${lbl} check digit is correct.`,
      printed_check: printed,
      computed_check: computed,
    });
  }

  // printed != computed: correction candidate; trust depends on context
  if (context === FieldContext.EQUIPMENT_ID || context === FieldContext.UNKNOWN) {
    return result({
      raw,
      normalized: norm,
      id_type: idType,
      status: Status.CORRECTED,
      confidence: "high",
      reason: `${lbl} check digit ${printed} -> ${computed}.`,
      printed_check: printed,
      computed_check: computed,
      corrected: correctedFull,
    });
  }

  if (context === FieldContext.FREE_TEXT) {
    if (knownOwnerPrefixes !== null && knownOwnerPrefixes.has(owner)) {
      return result({
        raw,
        normalized: norm,
        id_type: idType,
        status: Status.CORRECTED,
        confidence: "high",
        reason:
          `${lbl} check digit ${printed} -> ${computed}; ` +
          `owner prefix ${repr(owner)} corroborated against registry.`,
        printed_check: printed,
        computed_check: computed,
        corrected: correctedFull,
      });
    }
    return result({
      raw,
      normalized: norm,
      id_type: idType,
      status: Status.FLAGGED,
      confidence: "low",
      reason:
        `Free-text token has the ${lbl} shape but a failing check digit ` +
        `(${printed}; expected ${computed}). Not auto-corrected -- may not be a ` +
        `container number. Supply an owner registry to corroborate.`,
      printed_check: printed,
      computed_check: computed,
    });
  }

  // RAIL_VEHICLE context but BIC shape: expectation/structure mismatch
  return result({
    raw,
    normalized: norm,
    id_type: idType,
    status: Status.FLAGGED,
    confidence: "low",
    reason:
      `Token has ${lbl} shape but the field was declared RAIL_VEHICLE. ` +
      `Context/structure mismatch; not auto-corrected.`,
    printed_check: printed,
    computed_check: computed,
  });
}

// equipment_checkdigit.py:387-410
export function decideUic(
  raw: string,
  norm: string,
  context: FieldContext,
  printed: string,
  computed: string,
  correctedFull: string,
): CorrectionResult {
  if (context !== FieldContext.RAIL_VEHICLE) {
    const verdict =
      printed === computed ? "matches" : `fails (printed ${printed}, expected ${computed})`;
    return result({
      raw,
      normalized: norm,
      id_type: IdentifierType.UIC,
      status: Status.FLAGGED,
      confidence: "low",
      reason:
        `12-digit UIC candidate, but field context is not RAIL_VEHICLE. ` +
        `Luhn check ${verdict}; not auto-corrected -- a 12-digit run is not ` +
        `self-identifying.`,
      printed_check: printed,
      computed_check: computed,
    });
  }

  if (printed === computed) {
    return result({
      raw,
      normalized: norm,
      id_type: IdentifierType.UIC,
      status: Status.VALID,
      confidence: "high",
      reason: "UIC Luhn check digit is correct.",
      printed_check: printed,
      computed_check: computed,
    });
  }
  return result({
    raw,
    normalized: norm,
    id_type: IdentifierType.UIC,
    status: Status.CORRECTED,
    confidence: "high",
    reason: `UIC Luhn check digit ${printed} -> ${computed}.`,
    printed_check: printed,
    computed_check: computed,
    corrected: correctedFull,
  });
}

export interface X12Options {
  known_owner_prefixes?: ReadonlySet<string> | null;
  policy?: PolicyLike | null;
}

// equipment_checkdigit.py:417-495
export function correctX12Equipment(
  initial: string,
  number: string,
  printedCheck: string | null = null,
  options: X12Options = {},
): CorrectionResult {
  const init = normalize(initial);
  const num = number.replace(/[^0-9]/g, "");
  let knownOwnerPrefixes = options.known_owner_prefixes ?? null;
  const policy = options.policy ?? null;
  const rawPair = `${initial}/${number}`;

  if (policy !== null) {
    const t = policy.resolve(init.slice(0, 3));
    if (t.corroborated) {
      knownOwnerPrefixes = new Set([...(knownOwnerPrefixes ?? []), init.slice(0, 3)]);
    }
    if (t.force_flag) {
      const base = correctX12Equipment(initial, number, printedCheck, {
        known_owner_prefixes: knownOwnerPrefixes,
      });
      if (
        base.status === Status.VALID ||
        base.status === Status.CORRECTED ||
        base.status === Status.FLAGGED
      ) {
        return result({
          raw: base.raw,
          normalized: base.normalized,
          id_type: base.id_type,
          status: Status.FLAGGED,
          confidence: base.confidence,
          reason:
            `Owner prefix ${repr(init.slice(0, 3))} is on the operator deny list ` +
            `(review required, never auto-corrected). ` +
            (base.reason || ""),
          printed_check: base.printed_check,
          computed_check: base.computed_check,
        });
      }
      return base;
    }
  }

  // :453-458 -- `init.isalpha()`; the kernel's tokens are ASCII, so [A-Za-z]
  if (init.length !== 4 || !isAllAsciiAlpha(init)) {
    return result({
      raw: rawPair,
      normalized: init + num,
      id_type: IdentifierType.UNKNOWN,
      status: Status.INVALID_STRUCTURE,
      confidence: "high",
      reason: `X12 N7-01 expected 4 alpha (got ${repr(initial)}); cannot form ISO 6346 number.`,
    });
  }
  if (num.length > 6) {
    return result({
      raw: rawPair,
      normalized: init + num,
      id_type: IdentifierType.UNKNOWN,
      status: Status.FLAGGED,
      confidence: "low",
      reason: `X12 N7-02 has ${num.length} digits; not a standard 6-digit ISO 6346 serial.`,
    });
  }

  const body10 = init + num.padStart(6, "0");
  const { id_type, computed, nonstandard } = evaluateBic(body10);

  if (nonstandard) {
    return result({
      raw: rawPair,
      normalized: body10,
      id_type: IdentifierType.UNKNOWN,
      status: Status.FLAGGED,
      confidence: "low",
      reason:
        `X12 equipment category ${repr(init[3] as string)} is neither ISO 6346 nor ILU; ` +
        `suggested check digit ${computed}; not auto-applied.`,
      computed_check: computed,
    });
  }

  const lbl = label(id_type);
  if (printedCheck === null || printedCheck === "") {
    return result({
      raw: rawPair,
      normalized: body10,
      id_type,
      status: Status.CORRECTED,
      confidence: "high",
      reason: `X12 N7-18 (check digit) not transmitted; computed ${computed}. Write it to N7-18.`,
      printed_check: null,
      computed_check: computed,
      corrected: body10 + computed,
    });
  }
  if (printedCheck === computed) {
    return result({
      raw: rawPair,
      normalized: body10 + printedCheck,
      id_type,
      status: Status.VALID,
      confidence: "high",
      reason: `${lbl} check digit (N7-18) is correct.`,
      printed_check: printedCheck,
      computed_check: computed,
    });
  }
  return result({
    raw: rawPair,
    normalized: body10 + printedCheck,
    id_type,
    status: Status.CORRECTED,
    confidence: "high",
    reason: `${lbl} check digit (N7-18) ${printedCheck} -> ${computed}.`,
    printed_check: printedCheck,
    computed_check: computed,
    corrected: body10 + computed,
  });
}

// equipment_checkdigit.py:522-581 -- worked-math explainer
export interface ExplainChar {
  char: string;
  value: number;
  weight: number;
  product: number;
}
export interface ExplainStep {
  char: string;
  weight: number;
  product: number;
}
export type ExplainVerdict = "computed" | "valid" | "mismatch";
export type CategorySet = "iso6346" | "ilu" | "unknown";

export interface ExplainIso {
  ok: true;
  kind: "iso6346_ilu";
  normalized: string;
  body: string;
  printed: string | null;
  chars: ExplainChar[];
  sum: number;
  mod: number;
  remainder_ten: boolean;
  computed: number;
  category: string;
  category_set: CategorySet;
  verdict: ExplainVerdict;
  full: string;
}
export interface ExplainUic {
  ok: true;
  kind: "uic";
  normalized: string;
  body: string;
  printed: string | null;
  steps: ExplainStep[];
  sum: number;
  computed: number;
  verdict: ExplainVerdict;
  full: string;
}
export interface ExplainError {
  ok: false;
  error: string;
}
export type Explain = ExplainIso | ExplainUic | ExplainError;

export const EXPLAIN_SHAPE_ERROR =
  "Enter 4 letters + 6-7 digits (ISO 6346 / ILU) or 11-12 digits (UIC wagon).";

export function explain(token: string | null | undefined): Explain {
  const norm = normalize(token ?? "");
  if (!norm) return { ok: false, error: "Empty input." };

  // :540-556 -- 11 or 12 digits: UIC, mirrors uicCheckDigit step by step
  if (isAllAsciiDigits(norm) && (norm.length === 11 || norm.length === 12)) {
    const body = norm.slice(0, 11);
    const printed = norm.length === 12 ? (norm[11] as string) : null;
    const steps: ExplainStep[] = [];
    let total = 0;
    for (let i = 0; i < 11; i++) {
      const ch = body[10 - i] as string;
      let product = Number(ch) * (i % 2 === 0 ? 2 : 1);
      if (product >= 10) product = Math.floor(product / 10) + (product % 10);
      steps.push({ char: ch, weight: i % 2 === 0 ? 2 : 1, product });
      total += product;
    }
    const computed = (10 - (total % 10)) % 10;
    const verdict: ExplainVerdict =
      printed === null ? "computed" : printed === String(computed) ? "valid" : "mismatch";
    return {
      ok: true,
      kind: "uic",
      normalized: norm,
      body,
      printed,
      steps,
      sum: total,
      computed,
      verdict,
      full: body + String(computed),
    };
  }

  // :558-577 -- 4 letters + 6 or 7 digits, mirrors iso6346CheckDigit step by step
  if (/^[A-Z]{4}[0-9]{6,7}$/.test(norm)) {
    const body = norm.slice(0, 10);
    const printed = norm.length === 11 ? (norm[10] as string) : null;
    const chars: ExplainChar[] = [];
    let total = 0;
    for (let i = 0; i < 10; i++) {
      const ch = body[i] as string;
      const v = iso6346Value(ch);
      const w = 2 ** i;
      chars.push({ char: ch, value: v, weight: w, product: v * w });
      total += v * w;
    }
    const mod = total % 11;
    const computed = mod === 10 ? 0 : mod;
    const cat = body[3] as string;
    const category_set: CategorySet = ISO6346_CATEGORIES.has(cat)
      ? "iso6346"
      : ILU_CATEGORIES.has(cat)
        ? "ilu"
        : "unknown";
    const verdict: ExplainVerdict =
      printed === null ? "computed" : printed === String(computed) ? "valid" : "mismatch";
    return {
      ok: true,
      kind: "iso6346_ilu",
      normalized: norm,
      body,
      printed,
      chars,
      sum: total,
      mod,
      remainder_ten: mod === 10,
      computed,
      category: cat,
      category_set,
      verdict,
      full: body + String(computed),
    };
  }

  return { ok: false, error: EXPLAIN_SHAPE_ERROR };
}

/* CALC-PURE-END */
