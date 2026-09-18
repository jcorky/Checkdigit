import { explain, normalize, type Explain } from "./checkdigit";
import { validateToken, type SchemeChoice, type TokenValidation } from "./validation";
import { html, raw } from "../ui/dom";

/*
 * The result card shared by the home calculator and the detailed check page.
 * One input and one scheme choice produce one of seven render states — empty,
 * incomplete, calculated, matching, mismatched, unsupported category, invalid
 * format — each with its own copy, so the two pages never drift apart. The
 * eighth state in the brief, a copy failure, is handled by wireResultCopies.
 *
 * The card never overwrites the entered number with a suggestion, never
 * presents a mismatch candidate as a confirmed correction, and never turns an
 * invalid entry into a valid-looking completed identifier.
 */

export type ResultKind =
  | "empty"
  | "incomplete"
  | "calculated"
  | "matching"
  | "mismatch"
  | "unsupported"
  | "invalid";

export interface Anatomy {
  owner: string;
  category: string;
  serial: string;
  check: string;
  state: "calc" | "ok" | "bad" | "none";
}

export interface ResultView {
  kind: ResultKind;
  normalized: string;
  scheme: TokenValidation["scheme"];
  cardHtml: string;
  announce: string;
  anatomy: Anatomy | null;
  validation: TokenValidation | null;
  explanation: Explain | null;
}

export const SCHEME_LABEL: Record<string, string> = {
  iso6346: "ISO 6346 container",
  ilu: "ILU (EN 13044)",
  uic: "UIC wagon",
  unknown: "Unresolved category",
};

const LIMITATION =
  "This checks the format and check digit. It does not confirm that a container exists or is registered.";
const LIMITATION_HTML = `<p class="limitation">${LIMITATION}</p>`;

/* ---- small inline icons (decorative; state is also carried by words) ---- */
const ICON: Record<string, string> = {
  ok: `<svg class="ic" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`,
  calc: `<svg class="ic" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h2M8 15h2M14 11h2v6h-6"/></svg>`,
  bad: `<svg class="ic" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>`,
  review: `<svg class="ic" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.7 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>`,
};

const copyIcon = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>`;

export function copyBtn(value: string, label: string): string {
  return html`<button type="button" class="btn btn-ghost btn-small copy-btn" data-copy="${value}" aria-label="${label} ${value}">${raw(copyIcon)} ${label}</button>`;
}

const esc = (s: string): string => html`${s}`;

function rrow(key: string, valueHtml: string): string {
  return `<div class="rrow"><span class="rk">${esc(key)}</span><span class="rv">${valueHtml}</span></div>`;
}

/** Is the normalized text a valid prefix of a shape the user is still typing? */
function incompletePrefix(norm: string, scheme: SchemeChoice): boolean {
  if (scheme === "uic") return /^[0-9]{1,10}$/.test(norm);
  if (scheme === "iso6346" || scheme === "ilu") return /^[A-Z]{1,4}$/.test(norm) || /^[A-Z]{4}[0-9]{1,5}$/.test(norm);
  // auto: any prefix that could still become a full identifier
  return /^[A-Z]{1,4}$/.test(norm) || /^[A-Z]{4}[0-9]{1,5}$/.test(norm) || /^[0-9]{1,10}$/.test(norm);
}

function schemeMismatch(detected: TokenValidation["scheme"], choice: SchemeChoice): boolean {
  if (choice === "auto") return false;
  if (detected === "unknown") return false; // an unknown category is reported on its own terms
  return detected !== choice;
}

function invalidCard(normalized: string, raw_: string, message: string): { card: string; announce: string } {
  const shown = normalized || raw_;
  const entered = shown
    ? rrow("Entered", html`<span class="id">${shown}</span> ${raw(copyBtn(shown, "Copy entered"))}`)
    : "";
  const card =
    `<div class="result-card is-bad">` +
    `<p class="result-head is-bad">${(ICON.bad as string)} Not a valid number</p>` +
    `<div class="rgrid">${entered}${rrow("What to check", esc(message))}</div>` +
    `</div>`;
  return { card, announce: `${shown ? shown + ": " : ""}not a valid number. ${message}` };
}

export function buildResult(raw_: string, choice: SchemeChoice): ResultView {
  const normalized = normalize(raw_);
  const e = explain(raw_);
  const kernelScheme = validateToken(raw_, choice);
  const detected = kernelScheme.scheme;

  const base = {
    normalized,
    scheme: detected,
    validation: kernelScheme,
    explanation: e,
    anatomy: null as Anatomy | null,
  };

  // Empty
  if (!normalized) {
    return {
      ...base,
      kind: "empty",
      cardHtml:
        `<div class="empty-state"><span class="stripes" aria-hidden="true"></span>` +
        `<span>Enter a container number above, then choose <b>Check number</b>.</span></div>`,
      announce: "",
    };
  }

  // A scheme was forced that the shape contradicts.
  if (e.ok && schemeMismatch(detected, choice)) {
    const impliedLabel = SCHEME_LABEL[detected] ?? detected;
    const chosenLabel = SCHEME_LABEL[choice] ?? choice;
    const { card, announce } = invalidCard(
      normalized,
      raw_,
      `You chose ${chosenLabel}, but this has the shape of ${impliedLabel}. Change the identifier type or the number.`,
    );
    return { ...base, kind: "invalid", cardHtml: card, announce };
  }

  if (e.ok) {
    // Unsupported category: shape matches a container but the category letter
    // is neither ISO 6346 (U/J/Z) nor ILU (A/B/D/E/K).
    if (e.kind === "iso6346_ilu" && e.category_set === "unknown") {
      const card =
        `<div class="result-card is-review">` +
        `<p class="result-head is-review">${(ICON.review as string)} Unsupported category</p>` +
        `<p class="result-sub">Category letter <b>${esc(e.category)}</b> is neither an ISO 6346 container (U, J or Z) nor an ILU unit (A, B, D, E or K), so this is not a standard container number.</p>` +
        `<div class="rgrid">${rrow("Entered", html`<span class="id">${normalized}</span> ${raw(copyBtn(normalized, "Copy entered"))}`)}` +
        `${rrow("Check digit for this body under ISO mod-11", html`<b>${e.computed}</b> <span class="muted">(informational only)</span>`)}</div>` +
        `<p class="result-note">Confirm the category letter against your source. A check digit is shown for reference but this is not a recognised container or ILU identifier.</p>` +
        `</div>` +
        LIMITATION_HTML;
      return {
        ...base,
        kind: "unsupported",
        cardHtml: card,
        announce: `${normalized}: unsupported category ${e.category}. Not a standard container number.`,
        anatomy: {
          owner: e.body.slice(0, 3),
          category: e.category,
          serial: e.body.slice(4, 10),
          check: e.printed ?? "",
          state: "bad",
        },
      };
    }

    const kindLabel = SCHEME_LABEL[detected] ?? detected;
    const wholeLabel = e.kind === "uic" ? "first eleven digits" : "first ten characters";
    const anatomy: Anatomy | null =
      e.kind === "iso6346_ilu"
        ? {
            owner: e.body.slice(0, 3),
            category: e.category,
            serial: e.body.slice(4, 10),
            check: e.printed ?? String(e.computed),
            state: e.printed === null ? "calc" : e.verdict === "valid" ? "ok" : "bad",
          }
        : null;

    // Calculated: a body with no check digit.
    if (e.printed === null) {
      const card =
        `<div class="result-card is-calc">` +
        `<p class="result-head is-calc">${(ICON.calc as string)} Check digit calculated.</p>` +
        `<div class="big-digit"><span class="dbox" aria-hidden="true">${e.computed}</span>` +
        `<span class="dnote">The check digit for this body is <b>${e.computed}</b> under the ${kindLabel} rule. Confirm the body against your source — a wrong body still produces a check digit.</span></div>` +
        `<div class="rgrid">${rrow("Completed number", html`<span class="id big good">${e.full}</span>`)}</div>` +
        `<div class="result-actions">${copyBtn(e.full, "Copy full number")}</div>` +
        `</div>` +
        LIMITATION_HTML;
      return {
        ...base,
        kind: "calculated",
        cardHtml: card,
        announce: `Check digit calculated: ${e.computed}. Full number ${e.full}.`,
        anatomy,
      };
    }

    // Matching.
    if (e.verdict === "valid") {
      const card =
        `<div class="result-card is-ok">` +
        `<p class="result-head is-ok">${(ICON.ok as string)} Check digit matches.</p>` +
        `<div class="rgrid">${rrow("Full number", html`<span class="id big good">${normalized}</span> <span class="badge valid">${kindLabel}</span>`)}</div>` +
        `<div class="result-actions">${copyBtn(normalized, "Copy full number")}</div>` +
        `</div>` +
        LIMITATION_HTML;
      return {
        ...base,
        kind: "matching",
        cardHtml: card,
        announce: `${normalized}: check digit matches.`,
        anatomy,
      };
    }

    // Mismatch.
    const suggested = e.full;
    const card =
      `<div class="result-card is-bad">` +
      `<p class="result-head is-bad">${(ICON.bad as string)} Check digit mismatch</p>` +
      `<div class="rgrid">` +
      rrow("Entered", html`<span class="id big">${normalized}</span>`) +
      rrow("Entered digit", html`<b class="t-bad">${e.printed}</b>`) +
      rrow("Expected digit", html`<b class="t-ok">${e.computed}</b>`) +
      rrow(`If the ${wholeLabel} are correct`, html`<span class="id fix">${suggested}</span>`) +
      `</div>` +
      `<div class="result-actions">${copyBtn(suggested, "Copy suggested number")}</div>` +
      `<p class="result-note">Verify the whole number against your source; the error may be in the ${wholeLabel}. The suggested number is not a confirmed correction.</p>` +
      `</div>` +
      LIMITATION_HTML;
    return {
      ...base,
      kind: "mismatch",
      cardHtml: card,
      announce: `${normalized}: check digit mismatch. Entered ${e.printed}, expected ${e.computed}. If the ${wholeLabel} are correct, the number is ${suggested}.`,
      anatomy,
    };
  }

  // Not a recognised shape: incomplete, a stray letter in the serial, or invalid.
  if (incompletePrefix(normalized, choice)) {
    const need =
      /^[0-9]+$/.test(normalized)
        ? "Enter 11 digits to calculate a UIC check digit, or 12 to check one."
        : "Keep going — enter 10 characters to calculate the check digit, or 11 to check a complete number.";
    return {
      ...base,
      kind: "incomplete",
      cardHtml:
        `<div class="result-card is-neutral">` +
        `<p class="result-sub muted">${esc(need)}</p></div>`,
      announce: "",
    };
  }

  // A letter sitting where a digit belongs: never silently substitute it.
  const straySerial = /^[A-Z]{4}/.test(normalized) && /[A-Z]/.test(normalized.slice(4));
  if (straySerial) {
    const badChars = [...new Set(normalized.slice(4).match(/[A-Z]/g) ?? [])];
    const list = badChars.map((c) => `"${c}"`).join(", ");
    const { card, announce } = invalidCard(
      normalized,
      raw_,
      `The serial number should be digits only, but it contains ${list}. Letters are never replaced with digits automatically — check the number against your source (for example, the letter O versus the digit 0).`,
    );
    return { ...base, kind: "invalid", cardHtml: card, announce };
  }

  const { card, announce } = invalidCard(
    normalized,
    raw_,
    "Expected 4 letters and 6–7 digits for a container or ILU number, or 11–12 digits for a UIC wagon.",
  );
  return { ...base, kind: "invalid", cardHtml: card, announce };
}

/** Attach copy handlers to every .copy-btn under `root`. */
export function wireResultCopies(root: ParentNode, say: (msg: string) => void): void {
  for (const btn of root.querySelectorAll<HTMLButtonElement>(".copy-btn")) {
    btn.addEventListener("click", async () => {
      const text = btn.dataset["copy"] ?? "";
      try {
        await navigator.clipboard.writeText(text);
        say(`Copied ${text}.`);
        flash(btn, "Copied");
      } catch {
        say("Copy failed — select the number and copy it with Ctrl+C.");
        flash(btn, "Copy failed");
      }
    });
  }
}

function flash(btn: HTMLButtonElement, label: string): void {
  const original = btn.getAttribute("data-label") ?? btn.textContent ?? "";
  if (!btn.getAttribute("data-label")) btn.setAttribute("data-label", original);
  btn.textContent = label;
  window.setTimeout(() => {
    const svg = btn.dataset["copy"] ? copyIcon : "";
    btn.innerHTML = raw(svg).markup + " " + esc(btn.getAttribute("data-label") ?? "");
  }, 1400);
}
