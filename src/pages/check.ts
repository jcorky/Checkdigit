import "../styles/site.css";
import {
  correctIdentifier,
  explain,
  FieldContext,
  normalize,
  type ExplainIso,
  type ExplainUic,
} from "../lib/checkdigit";
import { badge, byId, html, raw } from "../ui/dom";

// Single-token calculator. The math comes from explain(); the verdict a file
// corrector would reach for the same token comes from correctIdentifier(), so
// the page shows both the arithmetic and the decision it feeds.

const input = byId<HTMLInputElement>("tok");
const out = byId("out");

function shareUrl(norm: string): string {
  return `${location.origin}/check#${norm}`;
}

function renderIso(e: ExplainIso): string {
  const cols = e.chars;
  const row = (label: string, cellsHtml: string, cls = "") =>
    `<tr class="${cls}"><td>${label}</td>${cellsHtml}</tr>`;
  const table = `
    <div class="tablewrap"><table class="math" aria-label="Per-character calculation">
      <tbody>
        ${row("char", cols.map((c) => html`<td>${c.char}</td>`).join(""), "chars")}
        ${row("value", cols.map((c) => html`<td>${c.value}</td>`).join(""))}
        ${row("weight 2^i", cols.map((c) => html`<td>${c.weight}</td>`).join(""))}
        ${row("product", cols.map((c) => html`<td>${c.product}</td>`).join(""))}
      </tbody>
    </table></div>`;

  const remainderNote = e.remainder_ten
    ? raw(' → <b>0</b> <span class="t-flag">(remainder 10 maps to check digit 0; ISO 6346 advises against issuing such serials)</span>')
    : "";
  const math = html`<p class="math-line">Σ = <b>${e.sum}</b> · ${e.sum} mod 11 = <b>${e.mod}</b>${remainderNote} · check digit <b class="cd">${e.computed}</b></p>`;

  const head = html`<div class="result-head">
      <span class="id">${e.body}<span class="cd">${e.computed}</span></span>
      ${raw(badge(e.category_set === "unknown" ? "flagged" : e.verdict === "mismatch" ? "corrected" : e.verdict === "valid" ? "valid" : "computed"))}
    </div>`;

  let verdict: string;
  if (e.verdict === "computed") {
    verdict = html`Full number: <span class="id">${e.full}</span>.`;
  } else if (e.verdict === "valid") {
    verdict = html`<span class="t-ok">Valid.</span> Printed ${e.printed} matches computed ${e.computed}.`;
  } else {
    verdict = html`<span class="t-fix">The digit you typed is wrong.</span> Printed ${e.printed}, computed ${e.computed}. If the body is right, the number is <span class="id">${e.full}</span>.`;
  }

  let category = "";
  if (e.category_set === "ilu") {
    category = html`<p class="note amber">Category "${e.category}" is ILU (EN 13044): same arithmetic, its own category set.</p>`;
  } else if (e.category_set === "unknown") {
    category = html`<p class="note flag">Category "${e.category}" is neither ISO 6346 (U, J, Z) nor ILU (A, B, D, E, K), so this number is <b>flagged</b>: the suggested digit ${e.computed} is shown but would never be applied automatically.</p>`;
  }

  const caveat =
    e.verdict === "mismatch"
      ? `<p class="note">A failing check means the digit <i>or</i> the body is wrong. Verify against the source document before correcting a single number on its own.</p>`
      : "";

  const decision = correctIdentifier(e.normalized, FieldContext.EQUIPMENT_ID);
  const asField =
    e.verdict === "computed"
      ? ""
      : html`<p class="verdict-line">In a declared equipment field this token is ${raw(badge(decision.status))} <span style="color:var(--muted)">${decision.reason}</span></p>`;

  return head + table + math + html`<p class="verdict-line">${raw(verdict)}</p>` + category + caveat + asField;
}

function renderUic(e: ExplainUic): string {
  // explain() lists steps right to left, as the Luhn algorithm walks them;
  // show them in reading order with the weight each digit received.
  const steps = [...e.steps].reverse();
  const row = (label: string, cellsHtml: string, cls = "") =>
    `<tr class="${cls}"><td>${label}</td>${cellsHtml}</tr>`;
  const table = `
    <div class="tablewrap"><table class="math" aria-label="Luhn steps">
      <tbody>
        ${row("digit", steps.map((s) => html`<td>${s.char}</td>`).join(""), "chars")}
        ${row("weight", steps.map((s) => html`<td>${s.weight}</td>`).join(""))}
        ${row("product (digits summed)", steps.map((s) => html`<td>${s.product}</td>`).join(""))}
      </tbody>
    </table></div>`;
  const math = html`<p class="math-line">Luhn mod 10 over the first 11 digits, weights 2,1,2,… from the right. Σ = <b>${e.sum}</b> · (10 − ${e.sum} mod 10) mod 10 = check digit <b class="cd">${e.computed}</b></p>`;
  const head = html`<div class="result-head">
      <span class="id">${e.body}<span class="cd">${e.computed}</span></span>
      ${raw(badge(e.verdict === "mismatch" ? "corrected" : e.verdict === "valid" ? "valid" : "computed"))}
      <span class="badge neutral">UIC wagon</span>
    </div>`;

  let verdict: string;
  if (e.verdict === "computed") {
    verdict = html`Full number: <span class="id">${e.full}</span>.`;
  } else if (e.verdict === "valid") {
    verdict = html`<span class="t-ok">Valid.</span> Printed ${e.printed} matches computed ${e.computed}.`;
  } else {
    verdict = html`<span class="t-fix">The digit you typed is wrong.</span> Printed ${e.printed}, computed ${e.computed}. If the body is right, the number is <span class="id">${e.full}</span>.`;
  }

  let asField = "";
  if (e.verdict !== "computed") {
    const rail = correctIdentifier(e.normalized, FieldContext.RAIL_VEHICLE);
    const elsewhere = correctIdentifier(e.normalized, FieldContext.UNKNOWN);
    asField =
      html`<p class="verdict-line">In a declared rail-vehicle field this token is ${raw(badge(rail.status))}. Anywhere else it is ${raw(badge(elsewhere.status))}: <span style="color:var(--muted)">${elsewhere.reason}</span></p>`;
  }

  return head + table + math + html`<p class="verdict-line">${raw(verdict)}</p>` + asField;
}

function render(): void {
  const value = input.value;
  const norm = normalize(value);
  if (norm) {
    history.replaceState(null, "", `#${norm}`);
  } else {
    history.replaceState(null, "", location.pathname);
  }

  if (!norm) {
    out.innerHTML = `<p class="empty-state">Nothing to check yet. Type a number above.</p>`;
    return;
  }
  const e = explain(value);
  if (!e.ok) {
    out.innerHTML = html`<p class="err">${e.error}</p>`;
    return;
  }
  const body = e.kind === "uic" ? renderUic(e) : renderIso(e);
  const share = html`<div class="share">
      <span>Share:</span> <code id="share-url">${shareUrl(norm)}</code>
      <button class="btn btn-ghost" type="button" id="copy">Copy link</button>
      <span id="copied" role="status" aria-live="polite"></span>
    </div>`;
  out.innerHTML = body + share;
  const copy = document.getElementById("copy");
  copy?.addEventListener("click", async () => {
    const note = document.getElementById("copied");
    try {
      await navigator.clipboard.writeText(shareUrl(norm));
      if (note) note.textContent = "Copied.";
    } catch {
      if (note) note.textContent = "Copy failed; select the link and copy it.";
    }
  });
}

function fromHash(): void {
  const h = location.hash.slice(1);
  if (h) {
    try {
      input.value = decodeURIComponent(h);
    } catch {
      input.value = h;
    }
  }
}

input.addEventListener("input", render);
window.addEventListener("hashchange", () => {
  fromHash();
  render();
});
fromHash();
render();
