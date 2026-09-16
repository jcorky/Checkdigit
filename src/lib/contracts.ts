import enums from "../../contracts/enums.json";
import findingsData from "../../contracts/findings.json";

/*
 * Typed access to the shared contract data under contracts/. The Python
 * service loads the same files (checkdigit/contracts.py); tests on both sides
 * assert that code and data agree.
 */

export const ENUMS = enums;

export type ValidationLayer = (typeof enums.validation_layer)[number];
export type ValidationState = (typeof enums.validation_state)[number];
export type ImportMode = (typeof enums.import_mode)[number];
export type JobState = (typeof enums.job_state)[number];
export type ProposalState = (typeof enums.proposal_state)[number];
export type FindingCode = (typeof findingsData.findings)[number]["code"];

export interface FindingDefinition {
  code: string;
  severity: "info" | "warning" | "blocking";
  blocking_scope: "none" | "record" | "feed_transformation" | "publication" | "transmission";
  explanation: string;
  recovery: string;
}

export const FINDINGS: readonly FindingDefinition[] = findingsData.findings as FindingDefinition[];

const byCode = new Map(FINDINGS.map((f) => [f.code, f]));

export function finding(code: string): FindingDefinition {
  const f = byCode.get(code);
  if (!f) throw new Error(`unknown finding code ${code}`);
  return f;
}

export function isEnumValue(name: keyof typeof enums, value: string): boolean {
  const values = enums[name];
  return Array.isArray(values) && (values as readonly string[]).includes(value);
}
