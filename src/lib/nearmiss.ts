/*
 * Port of checkdigit/nearmiss.py: optimal string alignment distance with early
 * exit, and ranking of check-valid candidates by body distance. Proposes; never
 * applies.
 */

export function osaDistance(a: string, b: string, maxDist = 2): number {
  if (a === b) return 0;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > maxDist) return maxDist + 1;
  let prev2: number[] | null = null;
  let prev: number[] = Array.from({ length: lb + 1 }, (_, j) => j);
  for (let i = 1; i <= la; i++) {
    const cur: number[] = [i, ...new Array<number>(lb).fill(0)];
    const ca = a[i - 1];
    for (let j = 1; j <= lb; j++) {
      const cost = ca === b[j - 1] ? 0 : 1;
      let v = Math.min((prev[j] as number) + 1, (cur[j - 1] as number) + 1, (prev[j - 1] as number) + cost);
      if (prev2 !== null && i > 1 && j > 1 && ca === b[j - 2] && a[i - 2] === b[j - 1]) {
        v = Math.min(v, (prev2[j - 2] as number) + 1);
      }
      cur[j] = v;
    }
    if (Math.min(...cur) > maxDist) return maxDist + 1;
    prev2 = prev;
    prev = cur;
  }
  const d = prev[lb] as number;
  return d <= maxDist ? d : maxDist + 1;
}

export interface Suggestion {
  eqid: string;
  distance: number;
  times_seen: number;
}

export function suggest(
  body10: string,
  pool: Iterable<[string, number]>,
  options: { exclude?: string; maxDistance?: number; limit?: number } = {},
): Suggestion[] {
  const exclude = options.exclude ?? "";
  const maxDistance = options.maxDistance ?? 2;
  const limit = options.limit ?? 3;
  const out: Suggestion[] = [];
  for (const [eqid, seen] of pool) {
    if (eqid === exclude) continue;
    const d = osaDistance(body10, eqid.slice(0, 10), maxDistance);
    if (d <= maxDistance) out.push({ eqid, distance: d, times_seen: seen });
  }
  out.sort((x, y) => x.distance - y.distance || y.times_seen - x.times_seen || (x.eqid < y.eqid ? -1 : x.eqid > y.eqid ? 1 : 0));
  return out.slice(0, limit);
}
