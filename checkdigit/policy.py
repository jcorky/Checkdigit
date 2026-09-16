"""
policy.py
=========
Pass 20: a user-facing rules/policy engine over the correction kernel.

The kernel already takes a global `owner_policy` (strict|lenient) and an
`known_owner_prefixes` allow-set per call. This module lifts that into a single
declarative object the OPERATOR controls -- because a terminal knows its own
quirks ("LEAU is a legitimate lessor prefix in our yard", "never auto-touch
anything starting with TEST", "be lenient with our own pseudo-prefixes") in a
way a generic default cannot.

Design (kept separate from the kernel on purpose -- correction logic stays pure):
a Policy is resolved PER OWNER PREFIX into an effective treatment, then handed
to the existing correctors via the two hooks they already accept. No kernel
change; policy is a layer, not a tangle.

Resolution order for a token's 3-letter owner prefix (first match wins):
  1. deny      -> NEVER auto-correct; always FLAG (even a valid-looking check).
                  Use for prefixes you want a human to eyeball no matter what.
  2. allow     -> treat as a corroborated owner (added to known_owner_prefixes),
                  so a failing check is CORRECTED even at low trust. Use for
                  legitimate lessor/pseudo prefixes your registry doesn't list.
  3. per-prefix owner_policy override (strict|lenient).
  4. default_policy (strict|lenient), applied to everything else.

Matching supports exact 3-letter prefixes and a single trailing wildcard, e.g.
"TES*" matches TESU/TEST.../etc. Wildcards are matched after exact keys.

A Policy round-trips to/from JSON so it can live in a file
(CHECKDIGIT_POLICY_FILE) or be posted to the API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from equipment_checkdigit import _OWNER_POLICIES


class PolicyError(ValueError):
    """Malformed policy (bad default, bad per-prefix value, bad shape)."""


@dataclass
class ResolvedTreatment:
    """The effective handling for one prefix."""
    owner_policy: str            # 'strict' | 'lenient'
    force_flag: bool             # deny-listed: always flag, never auto-correct
    corroborated: bool           # allow-listed: treat owner as known


@dataclass
class Policy:
    default_policy: str = "strict"
    allow: Set[str] = field(default_factory=set)        # prefixes / wildcards
    deny: Set[str] = field(default_factory=set)
    per_prefix: Dict[str, str] = field(default_factory=dict)  # prefix/wildcard -> policy

    # ---- validation ----
    def __post_init__(self):
        if self.default_policy not in _OWNER_POLICIES:
            raise PolicyError(
                f"default_policy must be one of {sorted(_OWNER_POLICIES)}, "
                f"got {self.default_policy!r}")
        for p, v in self.per_prefix.items():
            if v not in _OWNER_POLICIES:
                raise PolicyError(
                    f"per_prefix[{p!r}] must be one of {sorted(_OWNER_POLICIES)}, got {v!r}")
        overlap = {self._norm(x) for x in self.allow} & {self._norm(x) for x in self.deny}
        if overlap:
            raise PolicyError(f"prefixes in both allow and deny: {sorted(overlap)}")

    @staticmethod
    def _norm(p: str) -> str:
        return p.strip().upper()

    @staticmethod
    def _match(prefix: str, keys) -> Optional[str]:
        """Exact match first, then a single trailing-wildcard match (longest wins)."""
        prefix = prefix.upper()
        norm = {Policy._norm(k): k for k in keys}
        if prefix in norm:
            return norm[prefix]
        wild = sorted((k for k in norm if k.endswith("*")), key=len, reverse=True)
        for w in wild:
            if prefix.startswith(w[:-1]):
                return norm[w]
        return None

    def resolve(self, prefix: str) -> ResolvedTreatment:
        # Owner codes are 3 letters (ISO 6346 / ILU). Callers may pass a longer
        # token prefix (e.g. the 4-char "LEAU" category-bearing prefix); reduce to
        # the 3-letter owner so allow/deny/per-prefix keys (which are owner codes)
        # match regardless of how the caller sliced it. A trailing wildcard key is
        # matched against this normalized owner code.
        prefix = (prefix or "").upper()[:3]
        if self._match(prefix, self.deny) is not None:
            return ResolvedTreatment(self.default_policy, force_flag=True, corroborated=False)
        if self._match(prefix, self.allow) is not None:
            return ResolvedTreatment(self.default_policy, force_flag=False, corroborated=True)
        hit = self._match(prefix, self.per_prefix.keys())
        if hit is not None:
            return ResolvedTreatment(self.per_prefix[hit], force_flag=False, corroborated=False)
        return ResolvedTreatment(self.default_policy, force_flag=False, corroborated=False)

    # ---- serialization ----
    def to_dict(self) -> dict:
        return {"default_policy": self.default_policy,
                "allow": sorted(self.allow), "deny": sorted(self.deny),
                "per_prefix": dict(sorted(self.per_prefix.items()))}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Policy":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise PolicyError("policy must be a JSON object")
        try:
            return cls(
                default_policy=d.get("default_policy", "strict"),
                allow=set(d.get("allow", []) or []),
                deny=set(d.get("deny", []) or []),
                per_prefix=dict(d.get("per_prefix", {}) or {}))
        except (TypeError, AttributeError) as exc:
            raise PolicyError(f"malformed policy: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> "Policy":
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise PolicyError(f"policy is not valid JSON: {exc}") from exc

    @classmethod
    def load_file(cls, path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(fh.read())


def plan_for_tokens(policy: Policy, prefixes, *, base_known: Optional[Set[str]] = None
                    ) -> Tuple[Dict[str, str], Set[str], Set[str]]:
    """Pre-resolve a batch of owner prefixes into the inputs the correctors need.

    Returns (policy_by_prefix, allow_set, deny_set):
      * policy_by_prefix : prefix -> effective owner_policy (for reporting/insight)
      * allow_set        : base_known + every corroborated (allow-listed) prefix,
                           to pass as known_owner_prefixes
      * deny_set         : prefixes whose results must be downgraded to FLAGGED
    The service applies deny_set after correction by rewriting those dispositions,
    so a deny rule wins even over a structurally valid check digit.
    """
    policy_by_prefix: Dict[str, str] = {}
    allow_set: Set[str] = set(base_known or set())
    deny_set: Set[str] = set()
    for owner in {(pf or "").upper()[:3] for pf in prefixes}:
        t = policy.resolve(owner)
        policy_by_prefix[owner] = t.owner_policy
        if t.corroborated:
            allow_set.add(owner)
        if t.force_flag:
            deny_set.add(owner)
    return policy_by_prefix, allow_set, deny_set
