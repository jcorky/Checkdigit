import "../styles/site.css";
import { normalize, type ExplainIso, type ExplainUic } from "../lib/checkdigit";
import { analyzeText } from "../lib/segmented";
import { validateToken, type SchemeChoice, type TokenValidation } from "../lib/validation";
import { badge, byId, html, raw } from "../ui/dom";

// Single-number checker. The arithmetic comes from explain(); each validation
// layer reports its own state (src/lib/validation.ts); the verdict a file
// corrector would reach for the same token comes from correctIdentifier().

const input = byId<HTMLInputElement>("tok");
const schemeEl = byId<HTMLSelectElement>("scheme");
const noticeEl = byId("notice");
const liveEl = byId("live");
const out = byId("out");

const SCHEME_LABEL: Record<string, string> = {
  uic: "UIC wagon",
  ilu: "ILU (EN 13044)",
  iso6346: "ISO 6346 container",
  unknown: "Unknown category",
};

const LIMITATION = "A matching check digit does not verify registration, ownership, existence, location or release status.";

function copyBtn(value: string, what: string): string {
  return html`<button type="button" class="btn btn-ghost btn-small copy-btn" data-copy="${value}" aria-label="Copy ${what} ${value}">Copy</button>`;
}

function resultRow(key: string, valueHtml: string): string {
  return `<div class="rrow"><span class="rk">${escapeText(key)}</span><span class="rv">${valueHtml}</span></div>`;
}

const LAYER_LABEL: Record<string, string> = {
  structure: "Structure",
  check_digit: "Check digit",
  prefix_registration: "Prefix registration",
  equipment_record: "Equipment record",
  attribute_consistency: "Attribute consistency",
  operational_state: "Operational state",
  message_profile_acceptance: "Message / profile acceptance",
};

const STATE_LABEL: Record<string, string> = {
  passed: "Passed",
  failed: "Failed",
  warning: "Warning",
  not_checked: "Not checked",
  unavailable: "Unavailable",
  stale: "Stale",
  unsupported: "Unsupported",
  insufficient_information: "Insufficient information",
};

const SCHEMES = new Set(["iso6346", "ilu", "uic"]);

// The fragment carries the selected scheme so a shared check reopens under the
// same conditions: "#iso6346:CSQU3054384", or just "#CSQU3054384" for auto-detect.
function fragmentFor(norm: string, scheme: string): string {
  return scheme !== "auto" && SCHEMES.has(scheme) ? `${scheme}:${norm}` : norm;
}

function shareUrl(norm: string): string {
  return `${location.origin}/check#${fragmentFor(norm, schemeEl.value)}`;
}

function layersTable(v: TokenValidation): string {
  const rows = v.layers
    .map(
      (l) => html`<tr>
        <td class="layer-name">${LAYER_LABEL[l.layer] ?? l.layer}</td>
        <td>${raw(badge(l.state))} <span class="sr-only">${STATE_LABEL[l.state] ?? l.state}</span></td>
        <td class="layer-detail">${l.detail}</td>
        <td class="layer-src">${l.source}${l.rule_version !== "n/a" ? raw(`<br>${escapeText(l.rule_version)}`) : ""}</td>
      </tr>`,
    )
    .join("");
  return `<div class="tablewrap"><table class="layers" aria-label="Validation layers">
    <thead><tr><th>Layer</th><th>Result</th><th>Detail</th><th>Source · rule</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

const escapeText = (s: string): string => html`${s}`;

function mathIso(e: ExplainIso): string {
  const cols = e.chars;
  const row = (label: string, cellsHtml: string, cls = "") => `<tr class="${cls}"><td>${label}</td>${cellsHtml}</tr>`;
  const table = `<div class="tablewrap"><table class="math" aria-label="Per-character calculation"><tbody>
        ${row("char", cols.map((c) => html`<td>${c.char}</td>`).join(""), "chars")}
        ${row("value", cols.map((c) => html`<td>${c.value}</td>`).join(""))}
        ${row("weight 2^i", cols.map((c) => html`<td>${c.weight}</td>`).join(""))}
        ${row("product", cols.map((c) => html`<td>${c.product}</td>`).join(""))}
      </tbody></table></div>`;
  const remainderNote = e.remainder_ten
    ? raw(' → <b>0</b> <span class="t-flag">(remainder 10 maps to check digit 0; ISO 6346 advises against issuing such serials)</span>')
    : "";
  const math = html`<p class="math-line">Σ = <b>${e.sum}</b> · ${e.sum} mod 11 = <b>${e.mod}</b>${remainderNote} · check digit <b class="cd">${e.computed}</b></p>`;
  return table + math;
}

function mathUic(e: ExplainUic): string {
  const steps = [...e.steps].reverse();
  const row = (label: string, cellsHtml: string, cls = "") => `<tr class="${cls}"><td>${label}</td>${cellsHtml}</tr>`;
  const table = `<div class="tablewrap"><table class="math" aria-label="Luhn steps"><tbody>
        ${row("digit", steps.map((s) => html`<td>${s.char}</td>`).join(""), "chars")}
        ${row("weight", steps.map((s) => html`<td>${s.weight}</td>`).join(""))}
        ${row("product (digits summed)", steps.map((s) => html`<td>${s.product}</td>`).join(""))}
      </tbody></table></div>`;
  const math = html`<p class="math-line">Luhn mod 10 over the first 11 digits, weights 2,1,2,… from the right. Σ = <b>${e.sum}</b> · (10 − ${e.sum} mod 10) mod 10 = check digit <b class="cd">${e.computed}</b></p>`;
  return table + math;
}

function summaryText(v: TokenValidation): string {
  const lines = [`Checkdigit result for ${v.normalized}`, `scheme: ${v.scheme}`];
  for (const l of v.layers) lines.push(`${LAYER_LABEL[l.layer] ?? l.layer}: ${STATE_LABEL[l.state] ?? l.state} — ${l.detail}`);
  return lines.join("\n");
}

function render(): void {
  const value = input.value;
  const norm = normalize(value);
  history.replaceState(null, "", norm ? `#${fragmentFor(norm, schemeEl.value)}` : location.pathname);

  const n = analyzeText(value);
  if (n.rejected.length > 0) {
    noticeEl.hidden = false;
    const parts = n.rejected.map((r) => (r.count > 1 ? `"${r.char}" ×${r.count}` : `"${r.char}"`));
    noticeEl.innerHTML = html`<span class="notice-error">${parts.join(", ")} ${n.rejected.length === 1 && (n.rejected[0]?.count ?? 0) === 1 ? "is" : "are"} not identifier characters and ${n.rejected.length === 1 && (n.rejected[0]?.count ?? 0) === 1 ? "was" : "were"} not removed. Original kept: "${value}".</span>`;
  } else if (n.separatorsRemoved > 0 || n.uppercased > 0) {
    noticeEl.hidden = false;
    const bits: string[] = [];
    if (n.separatorsRemoved > 0) bits.push(`removed ${n.separatorsRemoved} separator${n.separatorsRemoved === 1 ? "" : "s"}`);
    if (n.uppercased > 0) bits.push(`upper-cased ${n.uppercased} letter${n.uppercased === 1 ? "" : "s"}`);
    noticeEl.innerHTML = html`<span class="notice-info">Normalized "${value}" → ${n.accepted} (${bits.join(", ")}).</span>`;
  } else {
    noticeEl.hidden = true;
    noticeEl.innerHTML = "";
  }

  if (!norm) {
    out.innerHTML = `<p class="empty-state"><span class="stripes" aria-hidden="true"></span><span>Nothing to check yet. Type or paste a number above.</span></p>`;
    liveEl.textContent = "";
    return;
  }
  const v = validateToken(value, schemeEl.value as SchemeChoice);
  const e = v.explanation;
  const schemeChip = html`<span class="badge neutral">${SCHEME_LABEL[v.scheme] ?? v.scheme}</span>`;
  const structure = v.layers.find((l) => l.layer === "structure");
  const categoryFlag =
    e && e.ok && e.kind === "iso6346_ilu" && v.scheme === "unknown" && e.verdict !== "valid"
      ? html`<p class="result-note t-flag">Category letter ${e.category} is neither ISO 6346 (U, J, Z) nor ILU (A, B, D, E, K). Flagged for review.</p>`
      : "";

  let summary = "";
  let math = "";
  let announce = "";

  if (e && e.ok) {
    math = e.kind === "uic" ? mathUic(e) : mathIso(e);
    const proposed = `${e.body}${e.computed}`;
    if (e.printed === null) {
      // A body with no check digit: compute and offer it, never assert it.
      summary =
        resultRow("Entered", html`<span class="id">${e.body}</span> ${raw(copyBtn(e.body, "the entered number"))}`) +
        resultRow("Result", html`<span class="t-fix">Calculated check digit: ${e.computed}.</span> ${raw(schemeChip)}`) +
        resultRow("Full number", html`<span class="id">${e.full}</span> ${raw(copyBtn(e.full, "the full number"))}`) +
        resultRow("Explanation", "This is the check digit the arithmetic gives for the body you entered. Confirm the body against your source; a wrong body can still produce a check digit.");
      announce = `${e.body}: calculated check digit ${e.computed}. Full number ${e.full}.`;
    } else if (e.verdict === "valid") {
      // A matching check digit never suppresses an unresolved category warning.
      const unknownCat = e.kind === "iso6346_ilu" && v.scheme === "unknown";
      const resultText = unknownCat
        ? html`<span class="t-ok">Check digit matches</span>; <span class="t-flag">category needs review.</span>`
        : html`<span class="t-ok">Check digit matches.</span>`;
      summary =
        resultRow("Entered", html`<span class="id">${v.normalized}</span> ${raw(copyBtn(v.normalized, "the entered number"))}`) +
        resultRow("Result", html`${raw(resultText)} ${raw(schemeChip)}`) +
        resultRow("Explanation", unknownCat
          ? `The printed check digit agrees with the body, but category letter ${e.category} is neither ISO 6346 (U, J, Z) nor ILU (A, B, D, E, K), so the scheme is unresolved.`
          : "The printed check digit agrees with the body. Structure and arithmetic pass.");
      announce = unknownCat
        ? `${v.normalized}: check digit matches, but the category needs review.`
        : `${v.normalized}: check digit matches.`;
    } else {
      // Mismatch: keep the entered number and the proposed number distinct.
      summary =
        resultRow("Entered", html`<span class="id">${v.normalized}</span> ${raw(copyBtn(v.normalized, "the entered number"))}`) +
        resultRow("Result", html`<span class="t-fix">Check digit mismatch.</span> ${raw(schemeChip)}`) +
        resultRow("Expected check digit for this prefix and serial", html`<b class="t-fix">${e.computed}</b>`) +
        resultRow("Proposed number", html`<span class="id">${proposed}</span> ${raw(copyBtn(proposed, "the proposed number"))}`) +
        resultRow("Explanation", "Verify the prefix and serial against your source before using the proposed number; either the body or the check digit may be wrong.");
      announce = `${v.normalized}: check digit mismatch. Expected ${e.computed} for this body. Proposed number ${proposed}.`;
    }
  } else {
    const reason = (structure && structure.detail) || (e && !e.ok ? e.error : "") || "Expected 4 letters, 6 digits and 1 check digit, or 11 to 12 digits for a UIC wagon.";
    summary =
      resultRow("Entered", html`<span class="id">${v.normalized || value}</span> ${raw(copyBtn(v.normalized || value, "the entered number"))}`) +
      resultRow("Result", html`<span class="t-bad">Not a recognized number shape.</span>`) +
      resultRow("Explanation", escapeText(reason));
    announce = `${v.normalized || value}: not a recognized number shape. ${reason}`;
  }

  const detail = html`<details class="explain"><summary>Show the arithmetic and every validation layer</summary>${raw(math)}${raw(layersTable(v))}</details>`;
  const share = html`<div class="share">
      <span>Permalink</span> <code id="share-url">${shareUrl(norm)}</code>
      <button class="btn btn-ghost btn-small" type="button" id="copy">Copy link</button>
      <button class="btn btn-ghost btn-small" type="button" id="copy-result">Copy result as text</button>
    </div>`;
  out.innerHTML =
    `<div class="result-summary">${summary}</div>` +
    categoryFlag +
    `<p class="result-limit">${escapeText(LIMITATION)}</p>` +
    detail +
    share;
  liveEl.textContent = announce;

  const say = (msg: string): void => {
    liveEl.textContent = msg;
  };
  for (const btn of out.querySelectorAll<HTMLButtonElement>(".copy-btn")) {
    btn.addEventListener("click", async () => {
      const text = btn.dataset["copy"] ?? "";
      try {
        await navigator.clipboard.writeText(text);
        say(`Copied ${text}.`);
      } catch {
        say("Copy failed; select the number and copy it.");
      }
    });
  }
  document.getElementById("copy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(shareUrl(norm));
      say("Link copied.");
    } catch {
      say("Copy failed; select the link and copy it.");
    }
  });
  document.getElementById("copy-result")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(summaryText(v));
      say("Result copied as text.");
    } catch {
      say("Copy failed.");
    }
  });
}

function fromHash(): void {
  let h = location.hash.slice(1);
  if (!h) return;
  try {
    h = decodeURIComponent(h);
  } catch {
    /* keep the raw fragment */
  }
  const sep = h.indexOf(":");
  if (sep > 0) {
    const scheme = h.slice(0, sep).toLowerCase();
    if (SCHEMES.has(scheme)) {
      schemeEl.value = scheme;
      input.value = h.slice(sep + 1);
      return;
    }
  }
  input.value = h;
}

input.addEventListener("input", render);
schemeEl.addEventListener("change", render);
window.addEventListener("hashchange", () => {
  fromHash();
  render();
});
fromHash();
render();
