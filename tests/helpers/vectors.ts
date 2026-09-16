import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "vitest";

import type {
  CorrectionResult,
  FieldContext,
  OwnerPolicy,
  PolicyLike,
  ResolvedTreatment,
} from "../../src/lib/checkdigit";

export const vectorsDir = resolve(import.meta.dirname, "..", "vectors");

export function loadVectors<T>(name: string): T {
  const path = resolve(vectorsDir, name);
  let rawText: string;
  try {
    rawText = readFileSync(path, "utf8");
  } catch (err) {
    throw new Error(`missing vector file ${path}; run "npm run vectors" (${String(err)})`);
  }
  return JSON.parse(rawText) as T;
}

export interface CalcVector {
  input: string;
  ok: boolean;
  kind?: "iso6346_ilu" | "uic";
  computed?: number;
  verdict?: string;
  sum?: number;
  full?: string;
  mod?: number;
  category_set?: string;
}

export interface PolicyDict {
  default_policy: OwnerPolicy;
  allow: string[];
  deny: string[];
  per_prefix: Record<string, OwnerPolicy>;
}

export interface DecisionCase {
  fn: "correct_identifier" | "correct_x12_equipment";
  token?: string;
  context?: FieldContext;
  owner_policy?: string;
  known_owner_prefixes?: string[] | null;
  initial?: string;
  number?: string;
  printed_check?: string | null;
  policy: string;
  result: CorrectionResult;
}

export interface DecisionVectors {
  cases: DecisionCase[];
  errors: { fn: string; arg: string; message: string }[];
  policies: Record<string, PolicyDict | null>;
}

/**
 * Minimal stand-in for policy.Policy.resolve (policy.py:80-106), enough to
 * drive the kernel's policy hooks. The full policy module is ported in Phase 2.
 */
export function policyFromDict(d: PolicyDict): PolicyLike {
  const norm = (p: string) => p.trim().toUpperCase();
  const match = (prefix: string, keys: string[]): string | null => {
    const table = new Map(keys.map((k) => [norm(k), k]));
    if (table.has(prefix)) return table.get(prefix) as string;
    const wild = [...table.keys()]
      .filter((k) => k.endsWith("*"))
      .sort((a, b) => b.length - a.length);
    for (const w of wild) if (prefix.startsWith(w.slice(0, -1))) return table.get(w) as string;
    return null;
  };
  return {
    resolve(prefix: string): ResolvedTreatment {
      const p = (prefix || "").toUpperCase().slice(0, 3);
      if (match(p, d.deny) !== null) {
        return { owner_policy: d.default_policy, force_flag: true, corroborated: false };
      }
      if (match(p, d.allow) !== null) {
        return { owner_policy: d.default_policy, force_flag: false, corroborated: true };
      }
      const hit = match(p, Object.keys(d.per_prefix));
      if (hit !== null) {
        return {
          owner_policy: d.per_prefix[hit] as OwnerPolicy,
          force_flag: false,
          corroborated: false,
        };
      }
      return { owner_policy: d.default_policy, force_flag: false, corroborated: false };
    },
  };
}

/** The subset of the kernel module a vector run needs; satisfied by the source and by the built chunk. */
export interface KernelLike {
  explain(token: string): { ok: boolean };
  correctIdentifier(
    token: string,
    context: FieldContext,
    options: {
      known_owner_prefixes?: ReadonlySet<string> | null;
      owner_policy?: string;
      policy?: PolicyLike | null;
    },
  ): CorrectionResult;
  correctX12Equipment(
    initial: string,
    number: string,
    printedCheck: string | null,
    options: { policy?: PolicyLike | null },
  ): CorrectionResult;
}

/** Runs every calc vector through `kernel.explain` and asserts the kernel-truth fields. */
export function runCalcVectors(kernel: KernelLike, vectors: CalcVector[]): void {
  for (const v of vectors) {
    const r = kernel.explain(v.input) as unknown as Record<string, unknown>;
    expect(r["ok"], v.input).toBe(v.ok);
    if (!r["ok"]) continue;
    expect(r["kind"], v.input).toBe(v.kind);
    expect(r["computed"], v.input).toBe(v.computed);
    expect(r["verdict"], v.input).toBe(v.verdict);
    expect(r["sum"], v.input).toBe(v.sum);
    expect(r["full"], v.input).toBe(v.full);
    if (r["kind"] === "iso6346_ilu") {
      expect(r["mod"], v.input).toBe(v.mod);
      expect(r["category_set"], v.input).toBe(v.category_set);
    }
  }
}

/** Runs every decision case through the kernel and asserts field-for-field equality. */
export function runDecisionCases(
  kernel: KernelLike,
  cases: DecisionCase[],
  policies: Map<string, PolicyLike | null>,
): void {
  for (const c of cases) {
    const policy = policies.get(c.policy);
    if (policy === undefined) throw new Error(`unknown policy ${c.policy}`);
    let actual: CorrectionResult;
    let label: string;
    if (c.fn === "correct_identifier") {
      const known = c.known_owner_prefixes ? new Set(c.known_owner_prefixes) : null;
      actual = kernel.correctIdentifier(c.token as string, c.context as FieldContext, {
        known_owner_prefixes: known,
        owner_policy: c.owner_policy as string,
        policy,
      });
      label = `${JSON.stringify(c.token)} ctx=${c.context} owner_policy=${c.owner_policy} known=${JSON.stringify(c.known_owner_prefixes)} policy=${c.policy}`;
    } else {
      actual = kernel.correctX12Equipment(
        c.initial as string,
        c.number as string,
        c.printed_check ?? null,
        { policy },
      );
      label = `x12 ${c.initial}/${c.number}/${JSON.stringify(c.printed_check)} policy=${c.policy}`;
    }
    expect(actual, label).toEqual(c.result);
  }
}

export function policiesFrom(data: DecisionVectors): Map<string, PolicyLike | null> {
  const policies = new Map<string, PolicyLike | null>();
  for (const [name, d] of Object.entries(data.policies)) {
    policies.set(name, d === null ? null : policyFromDict(d));
  }
  return policies;
}
