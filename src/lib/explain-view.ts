import { type Explain, type ExplainIso, type ExplainUic } from "./checkdigit";
import { type TokenValidation } from "./validation";
import { badge, html, raw } from "../ui/dom";

/*
 * Rendering for the worked arithmetic and the per-layer validation table.
 * Shared by the home calculator's "How this was calculated" disclosure and the
 * detailed check page so both show the same maths and the same layers.
 */

export const LAYER_LABEL: Record<string, string> = {
  structure: "Structure",
  check_digit: "Check digit",
  prefix_registration: "Prefix registration",
  equipment_record: "Equipment record",
  attribute_consistency: "Attribute consistency",
  operational_state: "Operational state",
  message_profile_acceptance: "Message / profile acceptance",
};

export const STATE_LABEL: Record<string, string> = {
  passed: "Passed",
  failed: "Failed",
  warning: "Warning",
  not_checked: "Not checked",
  unavailable: "Unavailable",
  stale: "Stale",
  unsupported: "Unsupported",
  insufficient_information: "Insufficient information",
};

const escapeText = (s: string): string => html`${s}`;

function mathIso(e: ExplainIso): string {
  const cols = e.chars;
  const row = (label: string, cellsHtml: string, cls = ""): string =>
    `<tr class="${cls}"><th scope="row">${label}</th>${cellsHtml}</tr>`;
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
  const row = (label: string, cellsHtml: string, cls = ""): string =>
    `<tr class="${cls}"><th scope="row">${label}</th>${cellsHtml}</tr>`;
  const table = `<div class="tablewrap"><table class="math" aria-label="Luhn steps"><tbody>
        ${row("digit", steps.map((s) => html`<td>${s.char}</td>`).join(""), "chars")}
        ${row("weight", steps.map((s) => html`<td>${s.weight}</td>`).join(""))}
        ${row("product (digits summed)", steps.map((s) => html`<td>${s.product}</td>`).join(""))}
      </tbody></table></div>`;
  const math = html`<p class="math-line">Luhn mod 10 over the first 11 digits, weights 2,1,2,… from the right. Σ = <b>${e.sum}</b> · (10 − ${e.sum} mod 10) mod 10 = check digit <b class="cd">${e.computed}</b></p>`;
  return table + math;
}

/** The worked arithmetic for the current number, or "" when there is none. */
export function mathHtml(e: Explain | null): string {
  if (!e || !e.ok) return "";
  return e.kind === "uic" ? mathUic(e) : mathIso(e);
}

export function layersTable(v: TokenValidation): string {
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
    <thead><tr><th scope="col">Layer</th><th scope="col">Result</th><th scope="col">Detail</th><th scope="col">Source · rule</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

const SCHEME_TEXT: Record<string, string> = {
  iso6346: "ISO 6346 container",
  ilu: "ILU (EN 13044)",
  uic: "UIC wagon",
  unknown: "unresolved category",
};

export function summaryText(v: TokenValidation): string {
  const lines = [`Checkdigit result for ${v.normalized}`, `Identifier type: ${SCHEME_TEXT[v.scheme] ?? v.scheme}`];
  for (const l of v.layers) lines.push(`${LAYER_LABEL[l.layer] ?? l.layer}: ${STATE_LABEL[l.state] ?? l.state} — ${l.detail}`);
  return lines.join("\n");
}
