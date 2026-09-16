import { correctIdentifier, FieldContext, Status, type CorrectionResult, type PolicyLike } from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag } from "../report";
import { applyEdits, type Edit } from "../substitution";
import { assertOwnerPolicy } from "./txt";

/*
 * Port of checkdigit/edifact_locator.py and edifact_corrector.py.
 * Only EQD with qualifier CN (EQD01 / 8053) is a container source; EQA is
 * deliberately excluded. UNA service string advice and the release character
 * are honoured. Offsets are string indices into the decoded text.
 */

export interface Separators {
  component: string;
  element: string;
  decimal: string;
  release: string;
  segment: string;
}

const DEFAULT_SEPARATORS: Separators = { component: ":", element: "+", decimal: ".", release: "?", segment: "'" };

// edifact_locator.py:47-53
export function detectSeparators(text: string): Separators {
  if (text.slice(0, 3) === "UNA" && text.length >= 9) {
    const s = text.slice(3, 9);
    return { component: s[0] as string, element: s[1] as string, decimal: s[2] as string, release: s[3] as string, segment: s[5] as string };
  }
  return { ...DEFAULT_SEPARATORS };
}

export interface Segment {
  tag: string;
  elements: string[][];
  raw: string;
  start: number;
}

// edifact_locator.py:64-94
export function splitSegments(text: string, sep: Separators): [string, number][] {
  const out: [string, number][] = [];
  let i = text.slice(0, 3) === "UNA" ? 9 : 0;
  let start = i;
  let buf: string[] = [];
  let escaped = false;
  const n = text.length;
  while (i < n) {
    const ch = text[i] as string;
    if (escaped) {
      buf.push(ch);
      escaped = false;
    } else if (ch === sep.release) {
      buf.push(ch);
      escaped = true;
    } else if (ch === sep.segment) {
      const seg = buf.join("");
      if (seg.trim()) out.push([seg, start]);
      i++;
      while (i < n && "\r\n \t".includes(text[i] as string)) i++;
      start = i;
      buf = [];
      continue;
    } else {
      buf.push(ch);
    }
    i++;
  }
  const tail = buf.join("");
  if (tail.trim()) out.push([tail, start]);
  return out;
}

// edifact_locator.py:97-112
export function splitOn(s: string, delim: string, release: string): string[] {
  const parts: string[] = [];
  let buf: string[] = [];
  let escaped = false;
  for (const ch of s) {
    if (escaped) {
      buf.push(ch);
      escaped = false;
    } else if (ch === release) {
      escaped = true;
    } else if (ch === delim) {
      parts.push(buf.join(""));
      buf = [];
    } else {
      buf.push(ch);
    }
  }
  parts.push(buf.join(""));
  return parts;
}

export function parseSegment(raw: string, start: number, sep: Separators): Segment {
  const elemsRaw = splitOn(raw, sep.element, sep.release);
  return {
    tag: elemsRaw[0] as string,
    elements: elemsRaw.map((e) => splitOn(e, sep.component, sep.release)),
    raw,
    start,
  };
}

export function iterSegments(text: string): Segment[] {
  const sep = detectSeparators(text);
  return splitSegments(text, sep).map(([raw, start]) => parseSegment(raw, start, sep));
}

export interface EquipmentRef {
  eqid: string;
  qualifier: string;
  size_type: string;
  raw_segment: string;
  seg_start: number;
  eqid_offset: number | null;
}

// edifact_locator.py:137-149
export function locateEquipment(text: string): EquipmentRef[] {
  const refs: EquipmentRef[] = [];
  for (const seg of iterSegments(text)) {
    if (seg.tag !== "EQD") continue;
    const els = seg.elements;
    const qualifier = els.length > 1 && els[1]?.length ? (els[1][0] as string) : "";
    const eqid = els.length > 2 && els[2]?.length ? (els[2][0] as string) : "";
    const sizeType = els.length > 3 && els[3]?.length ? (els[3][0] as string) : "";
    const eqidOffset = eqid ? seg.start + seg.raw.indexOf(eqid) : null;
    refs.push({ eqid, qualifier, size_type: sizeType, raw_segment: seg.raw, seg_start: seg.start, eqid_offset: eqidOffset });
  }
  return refs;
}

export interface EdifactOptions {
  owner_policy?: string;
  policy?: PolicyLike | null;
}

// edifact_locator.py:152-172
export function evaluateText(text: string, options: EdifactOptions = {}): [EquipmentRef, CorrectionResult | null][] {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  const out: [EquipmentRef, CorrectionResult | null][] = [];
  for (const ref of locateEquipment(text)) {
    if (ref.qualifier !== "CN") continue;
    if (!ref.eqid) {
      out.push([ref, null]);
      continue;
    }
    out.push([ref, correctIdentifier(ref.eqid, FieldContext.EQUIPMENT_ID, { owner_policy: ownerPolicy, policy: options.policy ?? null })]);
  }
  return out;
}

// edifact_corrector.py:23-63
export function correctEdifact(text: string, options: EdifactOptions = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const pairs = evaluateText(text, options);
  const edits: Edit[] = [];
  const changes: Change[] = [];
  const flags: Flag[] = [];
  let valid = 0;
  let empty = 0;
  let invalid = 0;
  for (const [ref, r] of pairs) {
    if (r === null) {
      empty++;
      continue;
    }
    if (r.status === Status.CORRECTED) {
      const corrected = r.corrected as string;
      const segmentAfter = ref.raw_segment.replace(ref.eqid, corrected);
      edits.push({ start: ref.eqid_offset as number, oldText: ref.eqid, newText: corrected });
      changes.push({
        old: ref.eqid,
        new: corrected,
        printed_check: r.printed_check ?? "",
        computed_check: r.computed_check ?? "",
        id_type: r.id_type,
        occurrences: [{ offset: ref.eqid_offset as number, label: "EQD/C237/8260", before: ref.raw_segment, after: segmentAfter }],
        reason: r.reason,
      });
    } else if (r.status === Status.VALID) {
      valid++;
    } else if (r.status === Status.FLAGGED) {
      flags.push({
        offset: ref.eqid_offset ?? -1,
        eqid: ref.eqid,
        suggested_check: r.computed_check ?? "",
        reason: r.reason,
        occurrences: 1,
      });
    } else {
      invalid++;
    }
  }
  const correctedText = applyEdits(text, edits);
  return makeReport({
    owner_policy: ownerPolicy,
    total_containers: pairs.length,
    corrected: changes,
    flagged: flags,
    valid,
    empty_id: empty,
    invalid,
    corrected_text: correctedText,
    containers: inventoryFromResults(pairs.filter((p): p is [EquipmentRef, CorrectionResult] => p[1] !== null).map(([, r]) => [r, r.status])),
  });
}
