import { normalize } from "./checkdigit";
import { validateToken, type TokenValidation } from "./validation";

/*
 * Several-number paste. Tokens are extracted with the free-text locator's
 * shape (four letters, seven digits) plus a documented extension: each cell
 * is first normalized (upper-cased, whitespace and hyphens removed) so a
 * lower-case or "MSKU 123456 5" token is found, and ten-character bodies and
 * eleven- or twelve-digit UIC numbers are accepted as whole cells. Nothing is
 * changed silently: the original cell text is kept beside its normalized form.
 */

export const BULK_CAP = 10_000;

export interface BulkRow {
  line: number;
  as_found: string;
  normalized: string;
  validation: TokenValidation;
}

export type ListMode = "validate" | "extract";

export interface BulkResult {
  rows: BulkRow[];
  refused: { count: number; cap: number } | null;
  cells_seen: number;
  /** Non-empty cells that yielded no identifier-shaped token (extract mode only). */
  excluded: { count: number; samples: string[] } | null;
}

const CELL_SPLIT = /[,\t;|]/;
const WHOLE = /^(?:[A-Z]{4}[0-9]{6,7}|[0-9]{11,12})$/;
const EMBEDDED = /[A-Z]{4}[0-9]{7}/g;

/**
 * Validate every supplied entry. One row per non-empty cell, including entries
 * whose shape is not recognised, which are reported as invalid structure rather
 * than dropped. Source line and original text are preserved.
 */
export function validateEntries(text: string): BulkResult {
  const rows: BulkRow[] = [];
  let cells = 0;
  const lines = text.split(/\r\n|\r|\n/);
  for (let li = 0; li < lines.length; li++) {
    for (const cell of (lines[li] as string).split(CELL_SPLIT)) {
      const raw = cell.trim();
      if (!raw) continue;
      cells++;
      if (rows.length >= BULK_CAP) {
        const remaining = cells - rows.length + countRemainingCells(lines, li);
        return { rows, refused: { count: rows.length + remaining, cap: BULK_CAP }, cells_seen: cells, excluded: null };
      }
      rows.push({ line: li + 1, as_found: raw, normalized: normalize(raw), validation: validateToken(raw) });
    }
  }
  return { rows, refused: null, cells_seen: cells, excluded: null };
}

export function extractTokens(text: string): BulkResult {
  const rows: BulkRow[] = [];
  let cells = 0;
  let excludedCount = 0;
  const excludedSamples: string[] = [];
  const lines = text.split(/\r\n|\r|\n/);
  outer: for (let li = 0; li < lines.length; li++) {
    const line = lines[li] as string;
    for (const cell of line.split(CELL_SPLIT)) {
      const raw = cell.trim();
      if (!raw) continue;
      cells++;
      const norm = normalize(raw);
      const found: string[] = [];
      if (WHOLE.test(norm)) found.push(norm);
      else for (const m of norm.matchAll(EMBEDDED)) found.push(m[0]);
      if (found.length === 0) {
        excludedCount++;
        if (excludedSamples.length < 5) excludedSamples.push(raw);
        continue;
      }
      for (const tok of found) {
        if (rows.length >= BULK_CAP) {
          const remaining = countRemaining(lines, li, cells);
          return { rows, refused: { count: rows.length + remaining, cap: BULK_CAP }, cells_seen: cells, excluded: excludedCount ? { count: excludedCount, samples: excludedSamples } : null };
        }
        rows.push({ line: li + 1, as_found: found.length === 1 ? raw : tok, normalized: tok, validation: validateToken(tok) });
      }
      if (rows.length >= BULK_CAP && li === lines.length - 1) break outer;
    }
  }
  return { rows, refused: null, cells_seen: cells, excluded: excludedCount ? { count: excludedCount, samples: excludedSamples } : null };
}

function countRemainingCells(lines: string[], fromLine: number): number {
  let n = 0;
  for (let li = fromLine + 1; li < lines.length; li++) {
    for (const cell of (lines[li] as string).split(CELL_SPLIT)) {
      if (cell.trim()) n++;
    }
  }
  return n;
}

function countRemaining(lines: string[], fromLine: number, _cells: number): number {
  let n = 0;
  for (let li = fromLine; li < lines.length; li++) {
    for (const cell of (lines[li] as string).split(CELL_SPLIT)) {
      const norm = normalize(cell.trim());
      if (!norm) continue;
      if (WHOLE.test(norm)) n++;
      else n += [...norm.matchAll(EMBEDDED)].length;
    }
  }
  return n;
}

export function summarize(rows: readonly BulkRow[]): Record<string, number> {
  const out: Record<string, number> = { total: rows.length, check_passed: 0, check_failed: 0, computed: 0, structure_failed: 0, flagged: 0 };
  for (const r of rows) {
    const check = r.validation.layers.find((l) => l.layer === "check_digit")?.state;
    const structure = r.validation.layers.find((l) => l.layer === "structure")?.state;
    if (structure === "failed") out["structure_failed"] = (out["structure_failed"] ?? 0) + 1;
    else if (check === "passed") out["check_passed"] = (out["check_passed"] ?? 0) + 1;
    else if (check === "failed") out["check_failed"] = (out["check_failed"] ?? 0) + 1;
    else if (check === "insufficient_information") out["computed"] = (out["computed"] ?? 0) + 1;
    if (r.validation.kernel?.status === "flagged") out["flagged"] = (out["flagged"] ?? 0) + 1;
  }
  return out;
}

const csvEscape = (v: string): string => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export const BULK_COLUMNS = ["as_found", "normalized", "id_type", "status", "printed_check", "computed_check", "suggested"] as const;

export function rowRecord(r: BulkRow): Record<(typeof BULK_COLUMNS)[number], string> {
  const e = r.validation.explanation;
  const k = r.validation.kernel;
  const structure = r.validation.layers.find((l) => l.layer === "structure")?.state;
  const check = r.validation.layers.find((l) => l.layer === "check_digit")?.state;
  const status = structure === "failed" ? "invalid_structure" : k?.status ?? (check === "insufficient_information" ? "computed" : "unknown");
  return {
    as_found: r.as_found,
    normalized: r.normalized,
    id_type: r.validation.scheme,
    status: check === "insufficient_information" ? "computed" : status,
    printed_check: e && e.ok ? (e.printed ?? "") : "",
    computed_check: e && e.ok ? String(e.computed) : "",
    suggested: e && e.ok ? e.full : "",
  };
}

export function toCsv(rows: readonly BulkRow[]): string {
  const lines = [BULK_COLUMNS.join(",")];
  for (const r of rows) {
    const rec = rowRecord(r);
    lines.push(BULK_COLUMNS.map((c) => csvEscape(rec[c])).join(","));
  }
  return "﻿" + lines.join("\r\n") + "\r\n";
}

export function toJson(rows: readonly BulkRow[]): string {
  return JSON.stringify(rows.map((r) => ({ line: r.line, ...rowRecord(r), layers: r.validation.layers })), null, 2);
}
