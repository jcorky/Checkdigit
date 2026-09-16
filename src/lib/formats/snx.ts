import { correctIdentifier, FieldContext, Status, type CorrectionResult, type PolicyLike } from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag, type Occurrence } from "../report";
import { applyEdits, type Edit } from "../substitution";
import { assertOwnerPolicy } from "./txt";

/*
 * Port of checkdigit/snx_locator.py and snx_corrector.py for terminal
 * container XML. The container number is denormalized across attributes that
 * must stay in sync (container/@eqid, equipment/@eqid, unit/@id,
 * unit/@unique-key, line-discharge-list/@unit-id). The document is never
 * re-serialized: attribute values are located by a scoped scan and spliced.
 *
 * Difference from the Python module: Python parses with ElementTree to count
 * identifier-bearing attributes; this port uses a small start-tag scanner with
 * balanced-tag and depth checks instead of a full XML parser (no DOM in a
 * worker, no dependency). Both refuse DOCTYPE and ENTITY declarations outright,
 * and both refuse to edit when the parsed count and the located count differ.
 */

export class XmlSecurityError extends Error {
  override name = "XmlSecurityError";
}
export class IntegrityError extends Error {
  override name = "IntegrityError";
}
export class XmlParseError extends Error {
  override name = "XmlParseError";
}

const MAX_DEPTH = 256;

const EQID_ATTRS = new Set(["container/eqid", "equipment/eqid", "unit/id", "unit/unique-key", "line-discharge-list/unit-id"]);
const SIZE_TYPE_ATTRS = new Set(["container/type", "equipment/type"]);
const SCAN_ATTR_NAMES = ["eqid", "id", "unique-key", "unit-id"]; // sorted, as in Python

export function harden(text: string): void {
  if (/<!DOCTYPE/i.test(text) || /<!ENTITY/i.test(text)) {
    throw new XmlSecurityError(
      "Refusing to parse XML containing a DOCTYPE/ENTITY declaration (XXE / external-DTD / entity-expansion hardening). Legitimate SNX has none.",
    );
  }
}

const localname = (name: string): string => {
  const afterNs = name.includes("}") ? (name.split("}").pop() as string) : name;
  return afterNs.includes(":") ? (afterNs.split(":").pop() as string) : afterNs;
};

interface StartTag {
  name: string;
  attrs: [string, string][];
}

/** Scan start tags in document order, checking balance and depth. */
export function scanStartTags(text: string): StartTag[] {
  const tags: StartTag[] = [];
  const stack: string[] = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    const lt = text.indexOf("<", i);
    if (lt === -1) break;
    if (text.startsWith("<!--", lt)) {
      const end = text.indexOf("-->", lt + 4);
      if (end === -1) throw new XmlParseError("unterminated comment");
      i = end + 3;
      continue;
    }
    if (text.startsWith("<![CDATA[", lt)) {
      const end = text.indexOf("]]>", lt + 9);
      if (end === -1) throw new XmlParseError("unterminated CDATA section");
      i = end + 3;
      continue;
    }
    if (text.startsWith("<?", lt)) {
      const end = text.indexOf("?>", lt + 2);
      if (end === -1) throw new XmlParseError("unterminated processing instruction");
      i = end + 2;
      continue;
    }
    const gt = findTagEnd(text, lt);
    if (gt === -1) throw new XmlParseError(`unterminated tag at offset ${lt}`);
    const body = text.slice(lt + 1, gt);
    if (body.startsWith("/")) {
      const closing = body.slice(1).trim();
      const open = stack.pop();
      if (open === undefined || open !== closing) {
        throw new XmlParseError(`mismatched closing tag </${closing}> at offset ${lt}`);
      }
      i = gt + 1;
      continue;
    }
    const selfClosing = body.endsWith("/");
    const inner = selfClosing ? body.slice(0, -1) : body;
    const nameMatch = /^([A-Za-z_][\w:.-]*)/.exec(inner);
    if (!nameMatch) throw new XmlParseError(`malformed tag at offset ${lt}`);
    const name = nameMatch[1] as string;
    const attrs: [string, string][] = [];
    const attrRe = /([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
    for (const m of inner.slice(name.length).matchAll(attrRe)) {
      attrs.push([m[1] as string, m[2] ?? m[3] ?? ""]);
    }
    tags.push({ name, attrs });
    if (!selfClosing) {
      stack.push(name);
      if (stack.length > MAX_DEPTH) throw new XmlParseError(`element nesting deeper than ${MAX_DEPTH}`);
    }
    i = gt + 1;
  }
  if (stack.length) throw new XmlParseError(`unclosed element <${stack[stack.length - 1]}>`);
  if (tags.length === 0) throw new XmlParseError("no elements found");
  return tags;
}

function findTagEnd(text: string, lt: number): number {
  let quote: string | null = null;
  for (let j = lt + 1; j < text.length; j++) {
    const ch = text[j] as string;
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === ">") {
      return j;
    }
  }
  return -1;
}

// snx_locator.py:99-113
export function parsedCounts(text: string): { counts: Map<string, number>; sizeTypes: Set<string> } {
  harden(text);
  const counts = new Map<string, number>();
  const sizeTypes = new Set<string>();
  for (const tag of scanStartTags(text)) {
    const ln = localname(tag.name);
    for (const [attr, val] of tag.attrs) {
      if (!val) continue;
      const key = `${ln}/${localname(attr)}`;
      if (EQID_ATTRS.has(key)) counts.set(val, (counts.get(val) ?? 0) + 1);
      else if (SIZE_TYPE_ATTRS.has(key)) sizeTypes.add(val);
    }
  }
  return { counts, sizeTypes };
}

const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// snx_locator.py:84-90
function elementOf(text: string, attrMatchStart: number): string {
  const lt = text.lastIndexOf("<", attrMatchStart);
  if (lt === -1) return "?";
  const m = /^[A-Za-z0-9_:.-]+/.exec(text.slice(lt + 1, lt + 60));
  return m ? localname(m[0]) : "?";
}

// snx_locator.py:116-124
export function locateOccurrences(text: string, eqid: string): [number, string][] {
  const pat = new RegExp(`\\b(?<attr>${SCAN_ATTR_NAMES.join("|")})="(?<val>${escapeRe(eqid)})"`, "g");
  const out: [number, string][] = [];
  for (const m of text.matchAll(pat)) {
    const attr = m.groups?.["attr"] as string;
    const valStart = m.index + m[0].indexOf('"') + 1;
    out.push([valStart, `${elementOf(text, m.index)}/@${attr}`]);
  }
  return out;
}

export interface SnxRef {
  eqid: string;
  occurrences: [number, string][];
}

// snx_locator.py:127-139
export function locateEquipment(text: string): SnxRef[] {
  const { counts } = parsedCounts(text);
  const refs: SnxRef[] = [];
  for (const [eqid, parsedN] of counts) {
    const occ = locateOccurrences(text, eqid);
    if (occ.length !== parsedN) {
      throw new IntegrityError(
        `occurrence mismatch for ${JSON.stringify(eqid)}: parser saw ${parsedN} id-bearing attribute(s) but the byte scan located ${occ.length}. Refusing to edit.`,
      );
    }
    refs.push({ eqid, occurrences: occ });
  }
  return refs;
}

export function sizeTypeCodes(text: string): string[] {
  return [...parsedCounts(text).sizeTypes].sort();
}

export interface SnxOptions {
  owner_policy?: string;
  policy?: PolicyLike | null;
}

export function evaluateText(text: string, options: SnxOptions = {}): [SnxRef, CorrectionResult][] {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  return locateEquipment(text).map((ref) => [
    ref,
    correctIdentifier(ref.eqid, FieldContext.EQUIPMENT_ID, { owner_policy: ownerPolicy, policy: options.policy ?? null }),
  ]);
}

// snx_corrector.py:23-61
export function correctSnx(text: string, options: SnxOptions = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const pairs = evaluateText(text, options);
  const edits: Edit[] = [];
  const changes: Change[] = [];
  const flags: Flag[] = [];
  let valid = 0;
  let invalid = 0;
  for (const [ref, r] of pairs) {
    if (r.status === Status.CORRECTED) {
      const corrected = r.corrected as string;
      const occurrences: Occurrence[] = [];
      for (const [offset, label] of ref.occurrences) {
        edits.push({ start: offset, oldText: ref.eqid, newText: corrected });
        const attr = label.split("/@").pop() as string;
        occurrences.push({ offset, label, before: `${attr}="${ref.eqid}"`, after: `${attr}="${corrected}"` });
      }
      changes.push({
        old: ref.eqid,
        new: corrected,
        printed_check: r.printed_check ?? "",
        computed_check: r.computed_check ?? "",
        id_type: r.id_type,
        occurrences,
        reason: r.reason,
      });
    } else if (r.status === Status.VALID) {
      valid++;
    } else if (r.status === Status.FLAGGED) {
      flags.push({
        offset: ref.occurrences.length ? (ref.occurrences[0] as [number, string])[0] : -1,
        eqid: ref.eqid,
        suggested_check: r.computed_check ?? "",
        reason: r.reason,
        occurrences: ref.occurrences.length,
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
    empty_id: 0,
    invalid,
    corrected_text: correctedText,
    containers: inventoryFromResults(pairs.map(([, r]) => [r, r.status])),
  });
}
