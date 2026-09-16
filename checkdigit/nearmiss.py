"""
nearmiss.py
===========
Near-miss candidate search for failing container numbers. Pure stdlib, no I/O.

Epistemics: a failing ISO 6346 check means the printed DIGIT or the BODY is
wrong. The deterministic corrector handles the first branch; this module serves
the second by searching identifiers the system has *actually seen* for bodies
within small edit distance of the failing body. It proposes -- it never applies.

Distance is optimal string alignment (OSA): Levenshtein plus adjacent
transposition at cost 1. Those are exactly the transcription-error classes the
ISO 6346 check digit exists to catch, so a check failure is most consistent
with one of them.

Distance semantics on 10-char bodies (check digits excluded from comparison):
  0  -- the exact body is known; only the printed check digit differed.
        The strongest possible signal.
  1  -- one substitution, insertion, deletion, or adjacent swap away.
  2  -- weaker; included but ranked below.

Callers are responsible for supplying a pool of CHECK-VALID canonical numbers
(suggesting one suspect number as the fix for another would be malpractice) and
for excluding the failing token's own full form.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


def osa_distance(a: str, b: str, max_dist: int = 2) -> int:
    """
    Optimal-string-alignment distance between a and b, with early exit: returns
    max_dist + 1 as soon as the true distance provably exceeds max_dist (length
    gap pre-check, then row-minimum band check). Exact for values <= max_dist.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    prev2: List[int] | None = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = min(prev[j] + 1,          # deletion
                    cur[j - 1] + 1,       # insertion
                    prev[j - 1] + cost)   # substitution / match
            if (prev2 is not None and i > 1 and j > 1
                    and ca == b[j - 2] and a[i - 2] == b[j - 1]):
                v = min(v, prev2[j - 2] + 1)   # adjacent transposition
            cur[j] = v
        if min(cur) > max_dist:
            return max_dist + 1
        prev2, prev = prev, cur
    return prev[lb] if prev[lb] <= max_dist else max_dist + 1


def suggest(body10: str,
            pool: Iterable[Tuple[str, int]],
            *,
            exclude: str = "",
            max_distance: int = 2,
            limit: int = 3) -> List[Dict[str, int | str]]:
    """
    Rank pool members by body edit distance to `body10`.

    pool     -- iterable of (eqid, times_seen); eqid is an 11-char CHECK-VALID
                canonical number (caller guarantees validity).
    exclude  -- the failing token's full normalized form; an identical pool
                entry is skipped (cannot suggest a token as its own fix).
    Returns at most `limit` dicts {eqid, distance, times_seen}, ordered by
    (distance asc, times_seen desc, eqid asc). Distance 0 means the body matched
    exactly and only the check digit differed.
    """
    out: List[Dict[str, int | str]] = []
    for eqid, seen in pool:
        if eqid == exclude:
            continue
        d = osa_distance(body10, eqid[:10], max_dist=max_distance)
        if d <= max_distance:
            out.append({"eqid": eqid, "distance": d, "times_seen": seen})
    out.sort(key=lambda s: (s["distance"], -int(s["times_seen"]), s["eqid"]))
    return out[:limit]
