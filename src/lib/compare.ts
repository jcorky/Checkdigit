import { extractTokens, type BulkRow } from "./bulk";

/*
 * Initial comparison: two identifier lists (pasted or extracted from files)
 * compared on a configurable key. Raw values are kept beside comparable
 * values; nothing is merged or deleted.
 */

export type CompareKey = "normalized" | "body";

export interface CompareSide {
  label: string;
  rows: BulkRow[];
  refused: { count: number; cap: number } | null;
}

export interface CompareEntry {
  key: string;
  a: BulkRow[];
  b: BulkRow[];
  outcome: "only_a" | "only_b" | "both" | "conflict" | "duplicate";
  note: string;
}

export interface CompareResult {
  key: CompareKey;
  entries: CompareEntry[];
  counts: Record<string, number>;
}

export function sideFromText(label: string, text: string): CompareSide {
  const res = extractTokens(text);
  return { label, rows: res.rows, refused: res.refused };
}

// Descriptive, neutral outcome labels. A comparison reports where a number
// appears; it makes no validation judgement, so none of these implies pass or
// fail. "Added" and "Removed" are deliberately absent: they would assert a
// direction the user has not established.
export function outcomeLabel(outcome: CompareEntry["outcome"], aLabel: string, bLabel: string): string {
  switch (outcome) {
    case "only_a":
      return `Only in ${aLabel}`;
    case "only_b":
      return `Only in ${bLabel}`;
    case "both":
      return "In both lists";
    case "conflict":
      return "Check-digit difference";
    case "duplicate":
      return "Repeated within a list";
  }
}

const keyOf = (r: BulkRow, key: CompareKey): string => {
  const n = r.normalized;
  if (key === "body") return /^[A-Z]{4}[0-9]{6,7}$/.test(n) ? n.slice(0, 10) : n;
  return n;
};

export function compare(a: CompareSide, b: CompareSide, key: CompareKey = "normalized"): CompareResult {
  const map = new Map<string, { a: BulkRow[]; b: BulkRow[] }>();
  for (const r of a.rows) {
    const k = keyOf(r, key);
    const e = map.get(k) ?? { a: [], b: [] };
    e.a.push(r);
    map.set(k, e);
  }
  for (const r of b.rows) {
    const k = keyOf(r, key);
    const e = map.get(k) ?? { a: [], b: [] };
    e.b.push(r);
    map.set(k, e);
  }
  const entries: CompareEntry[] = [];
  const counts: Record<string, number> = { only_a: 0, only_b: 0, both: 0, conflict: 0, duplicate: 0 };
  for (const [k, e] of map) {
    let outcome: CompareEntry["outcome"];
    let note = "";
    const forms = new Set([...e.a, ...e.b].map((r) => r.normalized));
    if (e.a.length && e.b.length) {
      if (key === "body" && forms.size > 1) {
        outcome = "conflict";
        note = `same body with different check digits: ${[...forms].join(" / ")}`;
      } else {
        outcome = "both";
        if (e.a.length !== e.b.length) note = `count differs: ${e.a.length} in ${a.label}, ${e.b.length} in ${b.label}`;
      }
    } else if (e.a.length) {
      outcome = "only_a";
    } else {
      outcome = "only_b";
    }
    if (outcome !== "conflict" && (e.a.length > 1 || e.b.length > 1)) {
      counts["duplicate"] = (counts["duplicate"] ?? 0) + 1;
      note = note ? `${note}; repeated within a list` : `repeated within a list (${e.a.length}× ${a.label}, ${e.b.length}× ${b.label})`;
    }
    counts[outcome] = (counts[outcome] ?? 0) + 1;
    entries.push({ key: k, a: e.a, b: e.b, outcome, note });
  }
  entries.sort((x, y) => (x.key < y.key ? -1 : x.key > y.key ? 1 : 0));
  return { key, entries, counts };
}

const csvEscape = (v: string): string => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export function compareCsv(res: CompareResult, aLabel: string, bLabel: string): string {
  const lines = [["key", "outcome", `${aLabel}_count`, `${bLabel}_count`, `${aLabel}_as_found`, `${bLabel}_as_found`, "note"].map(csvEscape).join(",")];
  for (const e of res.entries) {
    lines.push([e.key, e.outcome, String(e.a.length), String(e.b.length), e.a.map((r) => r.as_found).join(" | "), e.b.map((r) => r.as_found).join(" | "), e.note].map(csvEscape).join(","));
  }
  return "﻿" + lines.join("\r\n") + "\r\n";
}
