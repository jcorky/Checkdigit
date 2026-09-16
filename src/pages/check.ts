import "../styles/site.css";
import { correctIdentifier, FieldContext, normalize, type ExplainIso, type ExplainUic } from "../lib/checkdigit";
import { analyzeText } from "../lib/segmented";
import { validateToken, type SchemeChoice, type TokenValidation } from "../lib/validation";
import { badge, byId, html, raw } from "../ui/dom";

// Single-number checker. The arithmetic comes from explain(); each validation
// layer reports its own state (src/lib/validation.ts); the verdict a file
// corrector would reach for the same token comes from correctIdentifier().

const input = byId<HTMLInputElement>("tok");
const schemeEl = byId<HTMLSelectElement>("scheme");
const noticeEl = byId("notice");
const out = byId("out");

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

function shareUrl(norm: string): string {
  return `${location.origin}/check#${norm}`;
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
  history.replaceState(null, "", norm ? `#${norm}` : location.pathname);

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
    return;
  }
  const v = validateToken(value, schemeEl.value as SchemeChoice);
  const e = v.explanation;
  let head = "";
  let math = "";
  let asField = "";
  if (e && e.ok) {
    const structure = v.layers.find((l) => l.layer === "structure");
    const check = v.layers.find((l) => l.layer === "check_digit");
    const headBadge = structure?.state === "failed" ? "failed" : check?.state === "failed" ? "failed" : check?.state === "passed" ? "passed" : check?.state === "insufficient_information" ? "computed" : "warning";
    head = html`<div class="result-head">
      <span class="id">${e.body}<span class="cd">${e.computed}</span></span>
      ${raw(badge(headBadge))}
      <span class="badge neutral">${v.scheme === "uic" ? "UIC wagon" : v.scheme === "ilu" ? "ILU" : v.scheme === "iso6346" ? "ISO 6346" : "unknown category"}</span>
    </div>`;
    math = e.kind === "uic" ? mathUic(e) : mathIso(e);
    if (e.printed !== null) {
      if (e.kind === "uic") {
        const rail = correctIdentifier(v.normalized, FieldContext.RAIL_VEHICLE);
        const elsewhere = correctIdentifier(v.normalized, FieldContext.UNKNOWN);
        asField = html`<p class="verdict-line">In a declared rail-vehicle field this token is ${raw(badge(rail.status))}. Anywhere else it is ${raw(badge(elsewhere.status))}: <span class="muted">${elsewhere.reason}</span></p>`;
      } else if (v.kernel) {
        asField = html`<p class="verdict-line">In a declared equipment field this token is ${raw(badge(v.kernel.status))} <span class="muted">${v.kernel.reason}</span></p>`;
      }
    } else {
      asField = html`<p class="verdict-line">Expected check digit for this body: <b class="t-fix">${e.computed}</b>. Full number: <span class="id">${e.full}</span>. This is a candidate until the source confirms it.</p>`;
    }
  } else {
    head = html`<div class="result-head"><span class="id">${v.normalized}</span>${raw(badge("failed"))}</div>`;
  }

  const layers = layersTable(v);
  const explainBlock = math
    ? `<details class="explain" open><summary>Show the arithmetic</summary>${math}</details>`
    : "";
  const share = html`<div class="share">
      <span>Share:</span> <code id="share-url">${shareUrl(norm)}</code>
      <button class="btn btn-ghost btn-small" type="button" id="copy">Copy link</button>
      <button class="btn btn-ghost btn-small" type="button" id="copy-result">Copy result</button>
      <span id="copied" role="status" aria-live="polite"></span>
    </div>`;
  out.innerHTML = head + layers + asField + explainBlock + share;

  const note = () => document.getElementById("copied");
  document.getElementById("copy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(shareUrl(norm));
      const el = note();
      if (el) el.textContent = "Link copied.";
    } catch {
      const el = note();
      if (el) el.textContent = "Copy failed; select the link and copy it.";
    }
  });
  document.getElementById("copy-result")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(summaryText(v));
      const el = note();
      if (el) el.textContent = "Result copied as text.";
    } catch {
      const el = note();
      if (el) el.textContent = "Copy failed.";
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
schemeEl.addEventListener("change", render);
window.addEventListener("hashchange", () => {
  fromHash();
  render();
});
fromHash();
render();
