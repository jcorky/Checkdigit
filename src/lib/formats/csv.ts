import { correctIdentifier, FieldContext, Status, type CorrectionResult, type PolicyLike } from "../checkdigit";
import { inventoryFromResults, makeReport, type Change, type CorrectionReport, type Flag, type Occurrence } from "../report";
import { applyEdits, type Edit } from "../substitution";
import { assertOwnerPolicy, RE_BIC, RE_BIC_FULL } from "./txt";

/*
 * Port of checkdigit/csv_locator.py and csv_corrector.py: delimited tables
 * with column-scoped trust. RFC 4180 quoting is honoured, and every field keeps
 * the string span of its raw text so a token inside a targeted cell can be
 * spliced in place without re-serializing the file.
 */

export type ColumnSel = number | string;

export class CsvError extends Error {
  override name = "CsvError";
}

export interface CsvCell {
  row: number;
  col: number;
  value: string;
  val_start: number;
  val_end: number;
  quoted: boolean;
}

export interface CsvRef {
  eqid: string;
  offset: number;
  row: number;
  col: number;
  cell_quoted: boolean;
}

// csv_locator.py:67-75
export function sniffDelimiter(text: string, override?: string | null): string {
  if (override) return override;
  const first = text.split(/\r\n|\r|\n/).find((ln) => ln.trim()) ?? "";
  const counts: [string, number][] = [",", "\t", ";", "|"].map((d) => [d, first.split(d).length - 1]);
  let best = counts[0] as [string, number];
  for (const c of counts) if (c[1] > best[1]) best = c;
  return best[1] > 0 ? best[0] : ",";
}

// csv_locator.py:78-158
export function parseRecords(text: string, delimiter: string): CsvCell[][] {
  const rows: CsvCell[][] = [];
  let row: CsvCell[] = [];
  let i = 0;
  const n = text.length;
  let fieldStart = 0;
  let buf: string[] = [];
  let inQuotes = false;
  let quotedField = false;
  let r = 0;

  const endField = (endPos: number): void => {
    row.push({ row: r, col: row.length, value: buf.join(""), val_start: fieldStart, val_end: endPos, quoted: quotedField });
    buf = [];
    quotedField = false;
  };

  while (i < n) {
    const ch = text[i] as string;
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < n && text[i + 1] === '"') {
          buf.push('"');
          i += 2;
          continue;
        }
        inQuotes = false;
        i++;
        continue;
      }
      buf.push(ch);
      i++;
      continue;
    }
    if (ch === '"' && i === fieldStart) {
      inQuotes = true;
      quotedField = true;
      i++;
      continue;
    }
    if (ch === delimiter) {
      endField(i);
      i++;
      fieldStart = i;
      continue;
    }
    if (ch === "\r") {
      endField(i);
      rows.push(row);
      row = [];
      r++;
      i += i + 1 < n && text[i + 1] === "\n" ? 2 : 1;
      fieldStart = i;
      continue;
    }
    if (ch === "\n") {
      endField(i);
      rows.push(row);
      row = [];
      r++;
      i++;
      fieldStart = i;
      continue;
    }
    buf.push(ch);
    i++;
  }
  if (buf.length || row.length || fieldStart <= n) {
    if (i > fieldStart || buf.length || row.length) {
      endField(n);
      rows.push(row);
    }
  }
  const last = rows[rows.length - 1];
  if (last && last.length === 1 && (last[0] as CsvCell).value === "" && (last[0] as CsvCell).val_start >= n) rows.pop();
  return rows;
}

// csv_locator.py:159-177
export function resolveColumns(rows: CsvCell[][], columns: readonly ColumnSel[], hasHeader: boolean): number[] {
  const header = new Map<string, number>();
  if (hasHeader && rows.length) {
    for (const c of rows[0] as CsvCell[]) header.set(c.value.trim().toLowerCase(), c.col);
  }
  const out: number[] = [];
  for (const sel of columns) {
    if (typeof sel === "number") {
      out.push(sel);
    } else {
      const key = sel.trim().toLowerCase();
      if (!header.has(key)) {
        // message mirrors the Python repr: column 'x' not found in header row (have: ['a', 'b'])
        const pyRepr = (x: string) => (x.includes("'") && !x.includes('"') ? `"${x}"` : `'${x}'`);
        const have = header.size ? `[${[...header.keys()].sort().map(pyRepr).join(", ")}]` : "no header parsed";
        throw new CsvError(`column ${pyRepr(sel)} not found in header row (have: ${have})`);
      }
      out.push(header.get(key) as number);
    }
  }
  return out;
}

export interface CsvLocateOptions {
  delimiter?: string | null;
  has_header?: boolean;
  whole_cell?: boolean;
}

// csv_locator.py:179-215
export function locateCsv(text: string, columns: readonly ColumnSel[], options: CsvLocateOptions = {}): { refs: CsvRef[]; delimiter: string } {
  const delim = sniffDelimiter(text, options.delimiter);
  const hasHeader = options.has_header ?? true;
  const wholeCell = options.whole_cell ?? true;
  const rows = parseRecords(text, delim);
  const targets = new Set(resolveColumns(rows, columns, hasHeader));
  const refs: CsvRef[] = [];
  rows.forEach((rec, ri) => {
    if (hasHeader && ri === 0) return;
    for (const cell of rec) {
      if (!targets.has(cell.col)) continue;
      const raw = text.slice(cell.val_start, cell.val_end);
      if (wholeCell) {
        const stripped = cell.value.trim();
        if (!RE_BIC_FULL.test(stripped)) continue;
        const pos = raw.indexOf(stripped);
        if (pos < 0) continue;
        refs.push({ eqid: stripped, offset: cell.val_start + pos, row: ri, col: cell.col, cell_quoted: cell.quoted });
      } else {
        for (const m of raw.matchAll(RE_BIC)) {
          refs.push({ eqid: m[0], offset: cell.val_start + m.index, row: ri, col: cell.col, cell_quoted: cell.quoted });
        }
      }
    }
  });
  return { refs, delimiter: delim };
}

export interface CsvOptions extends CsvLocateOptions {
  trust?: boolean;
  owner_policy?: string;
  known_owner_prefixes?: ReadonlySet<string> | null;
  policy?: PolicyLike | null;
}

// csv_locator.py:218-235
export function evaluateCsv(text: string, columns: readonly ColumnSel[], options: CsvOptions = {}): { pairs: [CsvRef, CorrectionResult][]; delimiter: string } {
  const ownerPolicy = options.owner_policy ?? "strict";
  assertOwnerPolicy(ownerPolicy);
  const { refs, delimiter } = locateCsv(text, columns, options);
  const ctx = (options.trust ?? true) ? FieldContext.EQUIPMENT_ID : FieldContext.FREE_TEXT;
  const pairs: [CsvRef, CorrectionResult][] = refs.map((ref) => [
    ref,
    correctIdentifier(ref.eqid, ctx, {
      owner_policy: ownerPolicy,
      known_owner_prefixes: options.known_owner_prefixes ?? null,
      policy: options.policy ?? null,
    }),
  ]);
  return { pairs, delimiter };
}

// csv_corrector.py:22-71
export function correctCsv(text: string, columns: readonly ColumnSel[], options: CsvOptions = {}): CorrectionReport {
  const ownerPolicy = options.owner_policy ?? "strict";
  const { pairs } = evaluateCsv(text, columns, options);
  const groups = new Map<string, [CsvRef, CorrectionResult][]>();
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
    const first = items[0] as [CsvRef, CorrectionResult];
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
      occ.push({ offset: ref.offset, label: `row ${ref.row} col ${ref.col}`, before: eqid, after: r.corrected as string });
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
