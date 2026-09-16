import { correctIdentifier, explain, FieldContext, Status, type CorrectionResult, type Explain } from "./checkdigit";

/*
 * State and rules for the eleven-cell segmented identifier input. Pure
 * functions, no DOM: the page binds these to inputs and renders the view model.
 *
 * Normalization policy: only the kernel's own normalization is applied
 * silently (upper-casing, removal of whitespace and hyphens), and even that is
 * reported. Any other character is never discarded: the edit is refused, the
 * original text is shown, and the normalized form is offered as a proposal the
 * user can accept explicitly.
 */

export const CELL_COUNT = 11;

/** Eleven entries; each is "" or one character in [A-Z0-9]. */
export type Cells = string[];

export interface Normalization {
  original: string;
  /** Characters that survive approved normalization and are valid identifier characters. */
  accepted: string;
  /** Whitespace and hyphens removed (the kernel's normalization). */
  separatorsRemoved: number;
  /** Lower-case letters that were upper-cased. */
  uppercased: number;
  /** Characters outside [A-Z0-9] after normalization; never applied silently. */
  rejected: { char: string; count: number }[];
}

export type EditKind = "typed" | "segment" | "replace" | "rejected" | "overflow" | "noop" | "cleared";

export interface Notice {
  level: "info" | "warn" | "error";
  text: string;
  /** A normalized identifier the user may accept explicitly. */
  proposal?: string;
}

export interface EditResult {
  cells: Cells;
  kind: EditKind;
  notice: Notice | null;
  /** Cell index that should receive focus next, if any. */
  focus: number | null;
}

export type ViewState =
  | "empty"
  | "gap"
  | "partial"
  | "computed"
  | "valid"
  | "corrected"
  | "flagged"
  | "invalid";

export interface ViewModel {
  token: string;
  state: ViewState;
  hint: string;
  /** Number of characters still missing before the check digit can be judged (partial only). */
  missing: number;
  computedCheck: string | null;
  anatomy: { owner: string; category: string; serial: string; check: string };
  result: CorrectionResult | null;
  explanation: Explain | null;
}

export const emptyCells = (): Cells => Array.from({ length: CELL_COUNT }, () => "");

export function cellsFrom(token: string): Cells {
  const cells = emptyCells();
  for (let i = 0; i < Math.min(token.length, CELL_COUNT); i++) cells[i] = token[i] as string;
  return cells;
}

export const tokenOf = (cells: Cells): string => cells.join("");

export function analyzeText(text: string): Normalization {
  const separatorsRemoved = (text.match(/[\s-]/g) ?? []).length;
  const stripped = text.replace(/[\s-]/g, "");
  const uppercased = (stripped.match(/[a-z]/g) ?? []).length;
  const upper = stripped.toUpperCase();
  const counts = new Map<string, number>();
  let accepted = "";
  for (const ch of upper) {
    if (/^[A-Z0-9]$/.test(ch)) accepted += ch;
    else counts.set(ch, (counts.get(ch) ?? 0) + 1);
  }
  const rejected = [...counts.entries()].map(([char, count]) => ({ char, count }));
  return { original: text, accepted, separatorsRemoved, uppercased, rejected };
}

function describeRejected(n: Normalization): string {
  const parts = n.rejected.map((r) => (r.count > 1 ? `"${r.char}" ×${r.count}` : `"${r.char}"`));
  return `Not applied: ${parts.join(", ")} ${n.rejected.length === 1 && (n.rejected[0]?.count ?? 0) === 1 ? "is" : "are"} not identifier characters. Original kept: "${n.original}".`;
}

function describeNormalized(n: Normalization): string | null {
  const bits: string[] = [];
  if (n.separatorsRemoved > 0) bits.push(`removed ${n.separatorsRemoved} separator${n.separatorsRemoved === 1 ? "" : "s"}`);
  if (n.uppercased > 0) bits.push(`upper-cased ${n.uppercased} letter${n.uppercased === 1 ? "" : "s"}`);
  if (bits.length === 0) return null;
  return `Normalized "${n.original}" → ${n.accepted} (${bits.join(", ")}).`;
}

function overflow(cells: Cells, n: Normalization, room: number): EditResult {
  return {
    cells,
    kind: "overflow",
    notice: {
      level: "error",
      text: `Not applied: "${n.original}" has ${n.accepted.length} identifier characters but only ${room} fit. A container number is 11 characters (10 without its check digit).`,
    },
    focus: null,
  };
}

/** Replace the whole identifier with `text` (whole-number entry or a full paste). */
export function replaceAll(cells: Cells, text: string): EditResult {
  const n = analyzeText(text);
  if (n.rejected.length > 0) {
    const proposal = n.accepted.length >= 1 && n.accepted.length <= CELL_COUNT ? n.accepted : undefined;
    return {
      cells,
      kind: "rejected",
      notice: { level: "error", text: describeRejected(n), ...(proposal ? { proposal } : {}) },
      focus: null,
    };
  }
  if (n.accepted.length > CELL_COUNT) return overflow(cells, n, CELL_COUNT);
  if (n.accepted.length === 0) {
    if (text.length === 0) return { cells: emptyCells(), kind: "cleared", notice: null, focus: 0 };
    return { cells, kind: "noop", notice: null, focus: null };
  }
  const next = cellsFrom(n.accepted);
  const info = describeNormalized(n);
  return {
    cells: next,
    kind: "replace",
    notice: info ? { level: "info", text: info } : null,
    focus: Math.min(n.accepted.length, CELL_COUNT - 1),
  };
}

/**
 * Apply text entered into cell `index`. One character edits that cell only;
 * ten or more characters replace the whole identifier (a paste), clearing any
 * cell the paste does not cover; two to nine characters fill consecutive cells
 * from `index` and must fit, otherwise nothing is applied.
 */
export function editCell(cells: Cells, index: number, text: string): EditResult {
  const n = analyzeText(text);
  if (n.rejected.length > 0) {
    const proposal = n.accepted.length >= 10 && n.accepted.length <= CELL_COUNT ? n.accepted : undefined;
    return {
      cells,
      kind: "rejected",
      notice: { level: "error", text: describeRejected(n), ...(proposal ? { proposal } : {}) },
      focus: null,
    };
  }
  if (n.accepted.length === 0) return { cells, kind: "noop", notice: null, focus: null };
  if (n.accepted.length >= 10) return replaceAll(cells, text);
  if (n.accepted.length === 1) {
    const next = [...cells];
    next[index] = n.accepted;
    return { cells: next, kind: "typed", notice: null, focus: Math.min(index + 1, CELL_COUNT - 1) };
  }
  const room = CELL_COUNT - index;
  if (n.accepted.length > room) return overflow(cells, n, room);
  const next = [...cells];
  for (let i = 0; i < n.accepted.length; i++) next[index + i] = n.accepted[i] as string;
  const info = describeNormalized(n);
  return {
    cells: next,
    kind: "segment",
    notice: info ? { level: "info", text: info } : null,
    focus: Math.min(index + n.accepted.length, CELL_COUNT - 1),
  };
}

/** Backspace in an empty cell clears and focuses the previous one. */
export function backspace(cells: Cells, index: number): { cells: Cells; focus: number } {
  const next = [...cells];
  if (next[index] !== "") {
    next[index] = "";
    return { cells: next, focus: index };
  }
  if (index === 0) return { cells: next, focus: 0 };
  next[index - 1] = "";
  return { cells: next, focus: index - 1 };
}

export function evaluate(cells: Cells): ViewModel {
  const token = tokenOf(cells);
  const firstEmpty = cells.findIndex((c) => c === "");
  const contiguous = firstEmpty === -1 || cells.slice(firstEmpty).every((c) => c === "");
  const anatomy = {
    owner: cells.slice(0, 3).join(""),
    category: cells[3] ?? "",
    serial: cells.slice(4, 10).join(""),
    check: "",
  };
  const base = {
    token,
    missing: 0,
    computedCheck: null as string | null,
    anatomy,
    result: null as CorrectionResult | null,
    explanation: null as Explain | null,
  };

  if (token.length === 0) return { ...base, state: "empty", hint: "4 letters · 6 digits · 1 check" };
  if (!contiguous) return { ...base, state: "gap", hint: "fill from the left" };
  if (token.length < 10) {
    return { ...base, state: "partial", hint: "4 letters · 6 digits · 1 check", missing: 10 - token.length };
  }
  if (token.length === 10) {
    const e = explain(token);
    if (!e.ok || e.kind !== "iso6346_ilu") {
      return { ...base, state: "invalid", hint: "invalid structure", explanation: e };
    }
    const check = String(e.computed);
    return {
      ...base,
      state: "computed",
      hint: "check digit computed",
      computedCheck: check,
      anatomy: { ...anatomy, check },
      explanation: e,
    };
  }
  const r = correctIdentifier(token, FieldContext.EQUIPMENT_ID);
  const check = r.computed_check ?? "";
  const withResult = { ...base, computedCheck: r.computed_check, anatomy: { ...anatomy, check }, result: r };
  switch (r.status) {
    case Status.VALID:
      return { ...withResult, state: "valid", hint: "valid" };
    case Status.CORRECTED:
      return { ...withResult, state: "corrected", hint: "needs correction" };
    case Status.FLAGGED:
      return { ...withResult, state: "flagged", hint: "flagged" };
    default:
      return { ...withResult, state: "invalid", hint: "invalid structure" };
  }
}
