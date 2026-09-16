import type { CorrectionResult } from "./checkdigit";

/*
 * Port of checkdigit/correction_report.py: the shared result shape every
 * format corrector produces. Field names match the Python report so the two
 * surfaces compare directly. Offsets are UTF-16 string indices in this port.
 */

export const OFFSET_KIND = "utf16_unit";

export const IDENTITY_PRINTED_VALID = "printed_check_valid";
export const IDENTITY_MATH_CANDIDATE = "mathematical_candidate";
export const IDENTITY_UNVERIFIED = "unverified";

export interface Occurrence {
  offset: number;
  label: string;
  before: string;
  after: string;
}

export interface Change {
  old: string;
  new: string;
  printed_check: string;
  computed_check: string;
  id_type: string;
  occurrences: Occurrence[];
  reason: string;
}

export interface Flag {
  offset: number;
  eqid: string;
  suggested_check: string;
  reason: string;
  occurrences: number;
}

export interface NearMiss {
  eqid: string;
  distance: number;
  times_seen: number;
  provenance?: Record<string, string>;
}

export interface ContainerRecord {
  as_found: string;
  canonical: string;
  owner: string;
  category: string;
  id_type: string;
  status: string;
  printed_check: string;
  computed_check: string;
  occurrences: number;
  normalized: string;
  candidate: string | null;
  identity_basis: string;
  near_misses: NearMiss[];
}

export interface CorrectionReport {
  owner_policy: string;
  total_containers: number;
  corrected: Change[];
  flagged: Flag[];
  valid: number;
  empty_id: number;
  invalid: number;
  corrected_text: string | null;
  corrected_b64: string | null;
  containers: ContainerRecord[];
  offset_kind: string;
}

export function summary(report: CorrectionReport): Record<string, number> {
  return {
    containers: report.total_containers,
    corrected: report.corrected.length,
    flagged: report.flagged.length,
    valid: report.valid,
    empty_id: report.empty_id,
    invalid: report.invalid,
  };
}

export function makeReport(fields: Omit<CorrectionReport, "corrected_b64" | "containers" | "offset_kind"> & {
  corrected_b64?: string | null;
  containers?: ContainerRecord[];
}): CorrectionReport {
  return {
    ...fields,
    corrected_b64: fields.corrected_b64 ?? null,
    containers: fields.containers ?? [],
    offset_kind: OFFSET_KIND,
  };
}

// correction_report.py: inventory_from_results
export function inventoryFromResults(dispositions: readonly [CorrectionResult, string][]): ContainerRecord[] {
  const groups = new Map<string, ContainerRecord>();
  for (const [r, finalStatus] of dispositions) {
    const applied = finalStatus === "corrected" && !!r.corrected;
    const canonical = applied ? (r.corrected as string) : r.normalized;
    const candidate = r.corrected && r.corrected !== r.normalized ? r.corrected : null;
    const basis = finalStatus === "valid" ? IDENTITY_PRINTED_VALID : applied ? IDENTITY_MATH_CANDIDATE : IDENTITY_UNVERIFIED;
    const isBic = (r.id_type === "iso6346" || r.id_type === "ilu") && canonical.length >= 4;
    const key = JSON.stringify([canonical, r.raw, finalStatus]);
    const rec = groups.get(key);
    if (rec) {
      rec.occurrences += 1;
      continue;
    }
    groups.set(key, {
      as_found: r.raw,
      canonical,
      owner: isBic ? canonical.slice(0, 3) : "",
      category: isBic ? (canonical[3] as string) : "",
      id_type: r.id_type,
      status: finalStatus,
      printed_check: r.printed_check ?? "",
      computed_check: r.computed_check ?? "",
      occurrences: 1,
      normalized: r.normalized,
      candidate,
      identity_basis: basis,
      near_misses: [],
    });
  }
  return [...groups.values()];
}
