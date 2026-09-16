/*
 * Port of checkdigit/substitution.py: the only place source text is mutated.
 * Every edit is verified against the exact characters it expects to replace;
 * overlaps and mismatches throw instead of corrupting the file.
 *
 * Offsets here are JavaScript string indices (UTF-16 code units). They equal
 * Python's code-point offsets for any text without characters outside the
 * Basic Multilingual Plane; reports state `offset_kind: "utf16_unit"`.
 */

export interface Edit {
  start: number; // string index where `oldText` begins
  oldText: string; // exact substring expected at [start, start + oldText.length)
  newText: string;
}

export class SubstitutionError extends Error {
  override name = "SubstitutionError";
}

// substitution.py:32-55
export function applyEdits(text: string, edits: readonly Edit[]): string {
  const ordered = [...edits].sort((a, b) => a.start - b.start);
  const parts: string[] = [];
  let cursor = 0;
  for (const e of ordered) {
    if (e.start < cursor) {
      throw new SubstitutionError(
        `overlapping edit at offset ${e.start} (previous edit ended at ${cursor})`,
      );
    }
    const actual = text.slice(e.start, e.start + e.oldText.length);
    if (actual !== e.oldText) {
      throw new SubstitutionError(
        `edit verification failed at offset ${e.start}: expected ${JSON.stringify(e.oldText)}, found ${JSON.stringify(actual)}`,
      );
    }
    parts.push(text.slice(cursor, e.start), e.newText);
    cursor = e.start + e.oldText.length;
  }
  parts.push(text.slice(cursor));
  return parts.join("");
}

// substitution.py:58-62
export function changedIndices(a: string, b: string): number[] {
  if (a.length !== b.length) throw new Error("changedIndices requires equal-length strings");
  const out: number[] = [];
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) out.push(i);
  return out;
}
