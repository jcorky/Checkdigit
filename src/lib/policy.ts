import { OWNER_POLICIES, type OwnerPolicy, type PolicyLike, type ResolvedTreatment } from "./checkdigit";

/*
 * Port of checkdigit/policy.py: the operator's per-prefix rule layer.
 * Resolution order for a token's 3-letter owner prefix: deny, allow,
 * per-prefix override, default. Keys may end in a single trailing wildcard.
 * Session-only in the browser; nothing is persisted.
 */

export class PolicyError extends Error {
  override name = "PolicyError";
}

export interface PolicyDict {
  default_policy?: string;
  allow?: string[];
  deny?: string[];
  per_prefix?: Record<string, string>;
}

export class Policy implements PolicyLike {
  readonly default_policy: OwnerPolicy;
  readonly allow: Set<string>;
  readonly deny: Set<string>;
  readonly per_prefix: Record<string, OwnerPolicy>;

  constructor(d: PolicyDict = {}) {
    const def = d.default_policy ?? "strict";
    if (!OWNER_POLICIES.has(def)) {
      throw new PolicyError(`default_policy must be one of ['lenient', 'strict'], got ${JSON.stringify(def)}`);
    }
    this.default_policy = def as OwnerPolicy;
    this.allow = new Set(d.allow ?? []);
    this.deny = new Set(d.deny ?? []);
    this.per_prefix = {};
    for (const [p, v] of Object.entries(d.per_prefix ?? {})) {
      if (!OWNER_POLICIES.has(v)) {
        throw new PolicyError(`per_prefix[${JSON.stringify(p)}] must be one of ['lenient', 'strict'], got ${JSON.stringify(v)}`);
      }
      this.per_prefix[p] = v as OwnerPolicy;
    }
    const norm = (x: string) => x.trim().toUpperCase();
    const overlap = [...this.allow].map(norm).filter((x) => [...this.deny].map(norm).includes(x));
    if (overlap.length) throw new PolicyError(`prefixes in both allow and deny: ${JSON.stringify(overlap.sort())}`);
  }

  private static match(prefix: string, keys: Iterable<string>): string | null {
    const p = prefix.toUpperCase();
    const table = new Map<string, string>();
    for (const k of keys) table.set(k.trim().toUpperCase(), k);
    if (table.has(p)) return table.get(p) as string;
    const wild = [...table.keys()].filter((k) => k.endsWith("*")).sort((a, b) => b.length - a.length);
    for (const w of wild) if (p.startsWith(w.slice(0, -1))) return table.get(w) as string;
    return null;
  }

  // policy.py:92-106
  resolve(prefix: string): ResolvedTreatment {
    const p = (prefix || "").toUpperCase().slice(0, 3);
    if (Policy.match(p, this.deny) !== null) {
      return { owner_policy: this.default_policy, force_flag: true, corroborated: false };
    }
    if (Policy.match(p, this.allow) !== null) {
      return { owner_policy: this.default_policy, force_flag: false, corroborated: true };
    }
    const hit = Policy.match(p, Object.keys(this.per_prefix));
    if (hit !== null) {
      return { owner_policy: this.per_prefix[hit] as OwnerPolicy, force_flag: false, corroborated: false };
    }
    return { owner_policy: this.default_policy, force_flag: false, corroborated: false };
  }

  toDict(): Required<PolicyDict> {
    return {
      default_policy: this.default_policy,
      allow: [...this.allow].sort(),
      deny: [...this.deny].sort(),
      per_prefix: Object.fromEntries(Object.entries(this.per_prefix).sort()),
    };
  }
}
