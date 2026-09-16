import {
  correctIdentifier,
  correctX12Equipment,
  FieldContext,
  Status,
  type CorrectionResult,
  type PolicyLike,
} from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag } from "../report";
import { applyEdits, type Edit } from "../substitution";
import { assertOwnerPolicy } from "./txt";

/*
 * Port of checkdigit/x12_locator.py and x12_corrector.py.
 * N7-01 (initial) + N7-02 (number) with the check digit in N7-18 (element 761);
 * N9*EQ carries a full token. A Bill-of-Lading reference (N9*BM) is never
 * touched. X12 has no release character: tokenizing is a plain split.
 */

export interface Delims {
  element: string;
  subelement: string;
  segment: string;
}

const isAlnum = (ch: string): boolean => /^[\p{L}\p{N}]$/u.test(ch);

// x12_locator.py:47-71
export function detectDelims(text: string): Delims {
  if (text.slice(0, 3) === "ISA") {
    const ele = text.length > 3 ? (text[3] as string) : "*";
    if (text.length >= 106) {
      const comp = text[104] as string;
      const seg = text[105] as string;
      if (!isAlnum(seg) && seg !== " " && seg !== ele) return { element: ele, subelement: comp, segment: seg };
    }
    const nl = text.indexOf("\n");
    const seg = text.slice(0, 200).includes("~") ? "~" : nl !== -1 ? "\n" : "~";
    return { element: ele, subelement: ":", segment: seg };
  }
  return { element: "*", subelement: ":", segment: "~" };
}

export interface RawSegment {
  tag: string;
  elements: string[];
  offsets: number[];
  seg_start: number;
  raw: string;
}

// x12_locator.py:74-99
export function iterSegments(text: string, delims: Delims): RawSegment[] {
  const out: RawSegment[] = [];
  const { element: ele, segment: seg } = delims;
  const ws = "\r\n \t";
  let pos = 0;
  const n = text.length;
  while (pos < n) {
    while (pos < n && ws.includes(text[pos] as string)) pos++;
    if (pos >= n) break;
    let end = text.indexOf(seg, pos);
    if (end === -1) end = n;
    const raw = text.slice(pos, end);
    const elements: string[] = [];
    const offsets: number[] = [];
    let off = pos;
    for (const fld of raw.split(ele)) {
      elements.push(fld);
      offsets.push(off);
      off += fld.length + 1;
    }
    out.push({ tag: elements[0] ?? "", elements, offsets, seg_start: pos, raw });
    pos = end + seg.length;
  }
  return out;
}

export interface X12Ref {
  kind: "N7" | "N9EQ";
  label: string;
  seg_start: number;
  raw_segment: string;
  initial: string | null;
  number: string | null;
  check_value: string | null; // "" = empty slot present; null = slot absent
  check_offset: number | null;
  description: string;
  token: string | null;
  token_offset: number | null;
}

// x12_locator.py:119-139
export function locateEquipment(text: string): X12Ref[] {
  const delims = detectDelims(text);
  const refs: X12Ref[] = [];
  for (const { tag, elements: els, offsets: offs, seg_start, raw } of iterSegments(text, delims)) {
    if (tag === "N7") {
      const has18 = els.length > 18;
      refs.push({
        kind: "N7",
        label: "N7/N7-18(761)",
        seg_start,
        raw_segment: raw,
        initial: els.length > 1 ? (els[1] as string) : "",
        number: els.length > 2 ? (els[2] as string) : "",
        check_value: has18 ? (els[18] as string) : null,
        check_offset: has18 ? (offs[18] as number) : null,
        description: els.length > 22 ? (els[22] as string) : "",
        token: null,
        token_offset: null,
      });
    } else if (tag === "N9" && els.length > 1 && els[1] === "EQ") {
      refs.push({
        kind: "N9EQ",
        label: "N9*EQ",
        seg_start,
        raw_segment: raw,
        initial: null,
        number: null,
        check_value: null,
        check_offset: null,
        description: "",
        token: els.length > 2 ? (els[2] as string) : "",
        token_offset: els.length > 2 ? (offs[2] as number) : null,
      });
    }
  }
  return refs;
}

export interface X12Options {
  owner_policy?: string;
  policy?: PolicyLike | null;
}

// x12_locator.py:142-162
export function evaluateText(text: string, options: X12Options = {}): [X12Ref, CorrectionResult][] {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  const policy = options.policy ?? null;
  return locateEquipment(text).map((ref) => {
    if (ref.kind === "N7") {
      const printed = ref.check_value ? ref.check_value : null;
      return [ref, correctX12Equipment(ref.initial ?? "", ref.number ?? "", printed, { policy })];
    }
    return [ref, correctIdentifier(ref.token ?? "", FieldContext.EQUIPMENT_ID, { owner_policy: ownerPolicy, policy })];
  });
}

// x12_corrector.py:26-29
export function body10(initial: string | null, number: string | null): string {
  const init = (initial ?? "").toUpperCase();
  const digits = (number ?? "").replace(/[^0-9]/g, "");
  return init + digits.padStart(6, "0").slice(-6);
}

// x12_corrector.py:32-104
export function correctX12(text: string, options: X12Options = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const ele = detectDelims(text).element;
  const pairs = evaluateText(text, options);
  const edits: Edit[] = [];
  const changes: Change[] = [];
  const flags: Flag[] = [];
  const disp: [CorrectionResult, string][] = [];
  let valid = 0;
  let invalid = 0;
  for (const [ref, r] of pairs) {
    if (r.status === Status.VALID) {
      valid++;
      disp.push([r, "valid"]);
      continue;
    }
    if (r.status === Status.INVALID_STRUCTURE) {
      invalid++;
      disp.push([r, "invalid_structure"]);
      continue;
    }
    if (r.status === Status.FLAGGED) {
      const off = ref.kind === "N7" ? ref.check_offset : ref.token_offset;
      const eqid = ref.kind === "N7" ? body10(ref.initial, ref.number) : (ref.token ?? "");
      flags.push({ offset: off ?? ref.seg_start, eqid, suggested_check: r.computed_check ?? "", reason: r.reason, occurrences: 1 });
      disp.push([r, "flagged"]);
      continue;
    }
    if (ref.kind === "N7") {
      if (ref.check_offset === null) {
        flags.push({
          offset: ref.seg_start,
          eqid: body10(ref.initial, ref.number),
          suggested_check: r.computed_check ?? "",
          reason: `N7-18 (element 761) is absent; populating it would require inserting elements. Suggested check ${r.computed_check}; not auto-applied.`,
          occurrences: 1,
        });
        disp.push([r, "flagged"]);
        continue;
      }
      const oldText = ref.check_value ?? "";
      const newText = r.computed_check ?? "";
      const els = ref.raw_segment.split(ele);
      const afterSeg = [...els.slice(0, 18), newText, ...els.slice(19)].join(ele);
      const body = body10(ref.initial, ref.number);
      edits.push({ start: ref.check_offset, oldText, newText });
      changes.push({
        old: body + (r.printed_check ?? "_"),
        new: body + newText,
        printed_check: r.printed_check ?? "",
        computed_check: newText,
        id_type: r.id_type,
        occurrences: [{ offset: ref.check_offset, label: ref.label, before: ref.raw_segment, after: afterSeg }],
        reason: r.reason,
      });
      disp.push([r, "corrected"]);
    } else {
      const oldText = ref.token ?? "";
      const newText = r.corrected ?? "";
      const afterSeg = ref.raw_segment.replace(oldText, newText);
      edits.push({ start: ref.token_offset as number, oldText, newText });
      changes.push({
        old: oldText,
        new: newText,
        printed_check: r.printed_check ?? "",
        computed_check: r.computed_check ?? "",
        id_type: r.id_type,
        occurrences: [{ offset: ref.token_offset as number, label: ref.label, before: ref.raw_segment, after: afterSeg }],
        reason: r.reason,
      });
      disp.push([r, "corrected"]);
    }
  }
  const correctedText = applyEdits(text, edits);
  return makeReport({
    owner_policy: ownerPolicy,
    total_containers: pairs.length,
    corrected: changes,
    flagged: flags,
    valid,
    empty_id: 0,
    invalid,
    corrected_text: correctedText,
    containers: inventoryFromResults(disp),
  });
}
