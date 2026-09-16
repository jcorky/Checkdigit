import { correctIdentifier, FieldContext, Status, type CorrectionResult, type PolicyLike } from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag, type Occurrence } from "../report";
import { applyEdits, type Edit } from "../substitution";
import { assertOwnerPolicy, RE_BIC, RE_BIC_FULL } from "./txt";

/*
 * Port of checkdigit/fixedwidth_locator.py and fixedwidth_corrector.py:
 * fixed-width flat files with column-range trust. Ranges are 1-based inclusive
 * start and end columns. Line endings, padding and every other column are
 * preserved verbatim.
 */

export type ColRange = [number, number];

export class FixedWidthError extends Error {
  override name = "FixedWidthError";
}

export interface FwRef {
  eqid: string;
  offset: number;
  line: number;
  col_start: number;
}

// fixedwidth_locator.py:46-53
export function validateRanges(ranges: readonly ColRange[]): ColRange[] {
  const out: ColRange[] = [];
  for (const [a, b] of ranges) {
    if (a < 1 || b < 1 || a > b) throw new FixedWidthError(`invalid column range (${a},${b}); expected 1-based start<=end`);
    out.push([a, b]);
  }
  return out;
}

// fixedwidth_locator.py:56-64 (Python splitlines(keepends=True) over \r\n, \r, \n)
export function lineSpans(text: string): [number, number, number][] {
  const spans: [number, number, number][] = [];
  let pos = 0;
  let lineNo = 0;
  for (const m of text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g)) {
    const line = m[0];
    if (line.length === 0) break;
    lineNo++;
    const content = line.replace(/[\r\n]+$/, "");
    spans.push([lineNo, pos, content.length]);
    pos += line.length;
  }
  return spans;
}

export interface FwLocateOptions {
  header_lines?: number;
  whole_field?: boolean;
}

// fixedwidth_locator.py:67-99
export function locateFixedWidth(text: string, ranges: readonly ColRange[], options: FwLocateOptions = {}): FwRef[] {
  const rngs = validateRanges(ranges);
  const headerLines = options.header_lines ?? 0;
  const wholeField = options.whole_field ?? true;
  const refs: FwRef[] = [];
  for (const [lineNo, absStart, contentLen] of lineSpans(text)) {
    if (lineNo <= headerLines) continue;
    for (const [a, b] of rngs) {
      const s0 = a - 1;
      const e0 = Math.min(b, contentLen);
      if (s0 >= contentLen || s0 >= e0) continue;
      const raw = text.slice(absStart + s0, absStart + e0);
      if (wholeField) {
        const stripped = raw.trim();
        if (!RE_BIC_FULL.test(stripped)) continue;
        const pos = raw.indexOf(stripped);
        refs.push({ eqid: stripped, offset: absStart + s0 + pos, line: lineNo, col_start: s0 + pos + 1 });
      } else {
        for (const m of raw.matchAll(RE_BIC)) {
          refs.push({ eqid: m[0], offset: absStart + s0 + m.index, line: lineNo, col_start: s0 + m.index + 1 });
        }
      }
    }
  }
  return refs;
}

export interface FwOptions extends FwLocateOptions {
  trust?: boolean;
  owner_policy?: string;
  known_owner_prefixes?: ReadonlySet<string> | null;
  policy?: PolicyLike | null;
}

export function evaluateFixedWidth(text: string, ranges: readonly ColRange[], options: FwOptions = {}): [FwRef, CorrectionResult][] {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  const ctx = (options.trust ?? true) ? FieldContext.EQUIPMENT_ID : FieldContext.FREE_TEXT;
  return locateFixedWidth(text, ranges, options).map((ref) => [
    ref,
    correctIdentifier(ref.eqid, ctx, {
      owner_policy: ownerPolicy,
      known_owner_prefixes: options.known_owner_prefixes ?? null,
      policy: options.policy ?? null,
    }),
  ]);
}

// fixedwidth_corrector.py:20-69
export function correctFixedWidth(text: string, ranges: readonly ColRange[], options: FwOptions = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const pairs = evaluateFixedWidth(text, ranges, options);
  const groups = new Map<string, [FwRef, CorrectionResult][]>();
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
    const first = items[0] as [FwRef, CorrectionResult];
    const r0 = first[1];
    if (r0.status === Status.VALID) {
      valid++;
      continue;
    }
    if (r0.status === Status.INVALID_STRUCTURE) {
      invalid++;
      continue;
    }
    if (r0.status === Status.FLAGGED) {
      flags.push({ offset: first[0].offset, eqid, suggested_check: r0.computed_check ?? "", reason: r0.reason, occurrences: items.length });
      continue;
    }
    const occ: Occurrence[] = [];
    for (const [ref, r] of items) {
      edits.push({ start: ref.offset, oldText: eqid, newText: r.corrected as string });
      occ.push({ offset: ref.offset, label: `line ${ref.line} col ${ref.col_start}`, before: eqid, after: r.corrected as string });
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
