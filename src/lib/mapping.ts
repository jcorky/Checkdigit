import { parseRecords, sniffDelimiter } from "./formats/csv";
import { RE_BIC_FULL } from "./formats/txt";
import { makeFinding, type LocalFinding } from "./localjob";

/*
 * Mapping contracts and structural fingerprints for delimited feeds.
 *
 * The approved contract says which fields exist and how they are identified
 * (by header name or by position). The observed fingerprint is computed from
 * the file. They are compared, never confused: raw file hashes play no part.
 * Sampling never decides anything; every row is validated against the
 * identifier field contract, and a late violation prevents a completeness
 * claim.
 */

export interface MappingField {
  name: string;
  /** header name (identity mapping) or 0-based index (positional mapping) */
  identity: string | number;
  required: boolean;
  type: "identifier" | "text";
}

export interface MappingContract {
  version: number;
  positional: boolean;
  delimiter: string | null;
  has_header: boolean;
  fields: MappingField[];
  /** expected column count for positional contracts */
  column_count: number | null;
}

export interface StructuralFingerprint {
  delimiter: string;
  header: string[] | null;
  column_count: number;
  rows: number;
}

export interface MappingCheck {
  contract: MappingContract;
  observed: StructuralFingerprint;
  resolved: { field: string; column: number | null }[];
  findings: LocalFinding[];
  blocked: boolean;
  rows: number;
  violations: { row: number; column: number; value: string }[];
  treatment: string;
}

export function fingerprint(text: string, contract: MappingContract): StructuralFingerprint {
  const delimiter = sniffDelimiter(text, contract.delimiter);
  const rows = parseRecords(text, delimiter);
  const header = contract.has_header && rows.length ? (rows[0] as { value: string }[]).map((c) => c.value.trim()) : null;
  const widths = rows.map((r) => r.length);
  const columnCount = widths.length ? Math.max(...widths) : 0;
  return { delimiter, header, column_count: columnCount, rows: rows.length - (header ? 1 : 0) };
}

const MAX_ISOLATED_VIOLATIONS = 3;
const BLOCK_RATIO = 0.2;

export function checkMapping(text: string, contract: MappingContract): MappingCheck {
  const findings: LocalFinding[] = [];
  const observed = fingerprint(text, contract);
  const delimiter = observed.delimiter;
  const rows = parseRecords(text, delimiter);
  const resolved: { field: string; column: number | null }[] = [];
  let blocked = false;
  let treatment = "continue_identity_mapping";

  if (contract.positional) {
    if (contract.column_count !== null && observed.column_count !== contract.column_count) {
      blocked = true;
      treatment = "block_mapping_review";
      findings.push(makeFinding("MAPPING_DRIFT", `Positional contract expects ${contract.column_count} columns; the file has ${observed.column_count}. A column index cannot be reused once the field it represented may have moved.`));
    }
    for (const f of contract.fields) resolved.push({ field: f.name, column: blocked ? null : Number(f.identity) });
  } else {
    const header = observed.header ?? [];
    const lower = header.map((h) => h.toLowerCase());
    const dupes = lower.filter((h, i) => h && lower.indexOf(h) !== i);
    if (dupes.length) {
      blocked = true;
      treatment = "block_mapping_review";
      findings.push(makeFinding("MAPPING_DRIFT", `Duplicate header name${dupes.length === 1 ? "" : "s"} ${[...new Set(dupes)].map((d) => `"${d}"`).join(", ")}: a name that appears twice cannot identify a column.`, "header row"));
    }
    for (const f of contract.fields) {
      const idx = lower.indexOf(String(f.identity).toLowerCase());
      if (idx === -1) {
        resolved.push({ field: f.name, column: null });
        if (f.required) {
          blocked = true;
          treatment = "block_mapping_review";
          findings.push(makeFinding("MAPPING_DRIFT", `Required column "${f.identity}" is missing from the header (have: ${header.map((h) => `"${h}"`).join(", ") || "no header"}).`, "header row"));
        }
      } else {
        resolved.push({ field: f.name, column: idx });
      }
    }
    if (!blocked) {
      const order = contract.fields.map((f) => lower.indexOf(String(f.identity).toLowerCase()));
      const sorted = [...order].sort((a, b) => a - b);
      if (order.some((v, i) => v !== sorted[i])) {
        findings.push(makeFinding("MAPPING_REORDER_HARMLESS", `Columns are in a different order than the contract lists them; identity-based mapping continues.`));
      }
      const extra = header.filter((h) => h && !contract.fields.some((f) => String(f.identity).toLowerCase() === h.toLowerCase()));
      if (extra.length) findings.push(makeFinding("MAPPING_EXTENSION_PRESERVED", `Additional column${extra.length === 1 ? "" : "s"} ${extra.map((h) => `"${h}"`).join(", ")} preserved untouched.`));
    }
  }

  // Full-stream validation of every identifier field: empty or identifier-shaped.
  const violations: { row: number; column: number; value: string }[] = [];
  if (!blocked) {
    const idCols = contract.fields.filter((f) => f.type === "identifier").map((f) => resolved.find((r) => r.field === f.name)?.column).filter((c): c is number => c !== null && c !== undefined);
    rows.forEach((row, ri) => {
      if (contract.has_header && ri === 0) return;
      for (const col of idCols) {
        const cell = row[col];
        const value = cell ? cell.value.trim() : "";
        if (value !== "" && !RE_BIC_FULL.test(value.toUpperCase().replace(/[\s-]/g, ""))) {
          violations.push({ row: ri, column: col, value });
        }
      }
    });
    const dataRows = Math.max(1, rows.length - (contract.has_header ? 1 : 0));
    if (violations.length > MAX_ISOLATED_VIOLATIONS && violations.length / dataRows > BLOCK_RATIO) {
      blocked = true;
      treatment = "block_mapping_review";
      const first = violations[0] as { row: number; column: number; value: string };
      findings.push(makeFinding("MAPPING_VIOLATION_LATE", `${violations.length} of ${dataRows} rows carry a value in the identifier column that is not identifier-shaped (first: row ${first.row}, "${first.value}"). This looks like a layout change, not isolated bad records; the transformation is blocked until the mapping is reviewed.`, `row ${first.row}`));
    } else {
      for (const v of violations) {
        findings.push(makeFinding("MAPPING_VIOLATION_LATE", `Row ${v.row}, column ${v.column}: "${v.value}" is not identifier-shaped; the row is reported, not transformed.`, `row ${v.row}`));
      }
    }
  }
  return { contract, observed, resolved, findings, blocked, rows: observed.rows, violations, treatment };
}

/** A contract proposed from a header (a starting point the user reviews; never authoritative). */
export function proposeContract(text: string, delimiter: string | null = null): MappingContract {
  const delim = sniffDelimiter(text, delimiter);
  const rows = parseRecords(text, delim);
  const first = rows[0] ?? [];
  const headerLike = first.length > 0 && first.every((c) => c.value.trim() !== "" && !RE_BIC_FULL.test(c.value.trim()));
  const fields: MappingField[] = [];
  const sample = rows.slice(headerLike ? 1 : 0, headerLike ? 51 : 50);
  first.forEach((cell, i) => {
    const hits = sample.filter((r) => r[i] && RE_BIC_FULL.test((r[i] as { value: string }).value.trim().toUpperCase())).length;
    const isId = sample.length > 0 && hits / sample.length >= 0.5;
    fields.push({ name: headerLike ? cell.value.trim() : `column ${i}`, identity: headerLike ? cell.value.trim() : i, required: isId, type: isId ? "identifier" : "text" });
  });
  return { version: 1, positional: !headerLike, delimiter: delim, has_header: headerLike, fields, column_count: headerLike ? null : first.length };
}
