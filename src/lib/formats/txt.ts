import {
  correctIdentifier,
  FieldContext,
  OWNER_POLICIES,
  Status,
  type CorrectionResult,
  type PolicyLike,
} from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag, type Occurrence } from "../report";
import { applyEdits, type Edit } from "../substitution";

/*
 * Port of checkdigit/txt_locator.py and txt_corrector.py: the low-trust
 * free-text path. The ISO 6346 shape (4 upper-case letters + 7 digits) is
 * scanned globally as fixed 11-character, non-overlapping matches.
 */

// txt_locator.py:36
export const RE_BIC = /[A-Z]{4}[0-9]{7}/g;
export const RE_BIC_FULL = /^[A-Z]{4}[0-9]{7}$/;

export interface TxtRef {
  eqid: string;
  offset: number;
  line: number;
  col: number;
}

export interface TxtOptions {
  trust?: boolean;
  owner_policy?: string;
  known_owner_prefixes?: ReadonlySet<string> | null;
  policy?: PolicyLike | null;
}

export function assertOwnerPolicy(ownerPolicy: string): void {
  if (!OWNER_POLICIES.has(ownerPolicy)) {
    throw new Error(`owner_policy must be one of ['lenient', 'strict'], got ${JSON.stringify(ownerPolicy)}`);
  }
}

// txt_locator.py:47-50
function lineCol(text: string, offset: number): [number, number] {
  let line = 1;
  let lastNl = -1;
  for (let i = 0; i < offset; i++) {
    if (text.charCodeAt(i) === 10) {
      line++;
      lastNl = i;
    }
  }
  return [line, offset - lastNl];
}

export function locateEquipment(text: string): TxtRef[] {
  const refs: TxtRef[] = [];
  for (const m of text.matchAll(RE_BIC)) {
    const [line, col] = lineCol(text, m.index);
    refs.push({ eqid: m[0], offset: m.index, line, col });
  }
  return refs;
}

export function evaluateText(text: string, options: TxtOptions = {}): [TxtRef, CorrectionResult][] {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  const ctx = options.trust ? FieldContext.EQUIPMENT_ID : FieldContext.FREE_TEXT;
  return locateEquipment(text).map((ref) => [
    ref,
    correctIdentifier(ref.eqid, ctx, {
      owner_policy: ownerPolicy,
      known_owner_prefixes: options.known_owner_prefixes ?? null,
      policy: options.policy ?? null,
    }),
  ]);
}

// txt_corrector.py
export function correctTxt(text: string, options: TxtOptions = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const pairs = evaluateText(text, options);
  const groups = new Map<string, [TxtRef, CorrectionResult][]>();
  for (const pair of pairs) {
    const list = groups.get(pair[0].eqid);
    if (list) list.push(pair);
    else groups.set(pair[0].eqid, [pair]);
  }
  const edits: Edit[] = [];
  const changes: Change[] = [];
  const flags: Flag[] = [];
  let valid = 0;
  let invalid = 0;
  for (const [eqid, items] of groups) {
    const r0 = (items[0] as [TxtRef, CorrectionResult])[1];
    if (r0.status === Status.VALID) {
      valid++;
      continue;
    }
    if (r0.status === Status.INVALID_STRUCTURE) {
      invalid++;
      continue;
    }
    if (r0.status === Status.FLAGGED) {
      flags.push({
        offset: (items[0] as [TxtRef, CorrectionResult])[0].offset,
        eqid,
        suggested_check: r0.computed_check ?? "",
        reason: r0.reason,
        occurrences: items.length,
      });
      continue;
    }
    const occ: Occurrence[] = [];
    for (const [ref, r] of items) {
      edits.push({ start: ref.offset, oldText: eqid, newText: r.corrected as string });
      occ.push({ offset: ref.offset, label: `line ${ref.line}:${ref.col}`, before: eqid, after: r.corrected as string });
    }
    changes.push({
      old: eqid,
      new: r0.corrected as string,
      printed_check: r0.printed_check ?? "",
      computed_check: r0.computed_check ?? "",
      id_type: r0.id_type,
      occurrences: occ,
      reason: r0.reason,
    });
  }
  const correctedText = applyEdits(text, edits);
  return makeReport({
    owner_policy: ownerPolicy,
    total_containers: groups.size,
    corrected: changes,
    flagged: flags,
    valid,
    empty_id: 0,
    invalid,
    corrected_text: correctedText,
    containers: inventoryFromResults(pairs.map(([, r]) => [r, r.status])),
  });
}
