import "../styles/site.css";
import "../styles/app.css";
import { explain, ISO6346_LETTER_VALUES, iso6346Value } from "../lib/checkdigit";
import { decodeSizeType, VERIFY_NOTES } from "../lib/sizetype";
import { validateToken } from "../lib/validation";
import { byId, html, raw } from "../ui/dom";

// The reference library: static articles with sources and review dates, a
// letter table generated from the kernel's own values, an interactive
// blind-spot demonstrator, the size/type decoder and a quick checker.

const REVIEWED = "2026-09-16";

interface Article {
  id: string;
  title: string;
  source: string;
  body: () => string;
}

const letterTable = (): string =>
  `<table><thead><tr><th>Letter</th><th>Value</th><th>Letter</th><th>Value</th></tr></thead><tbody>` +
  Array.from({ length: 13 }, (_, i) => {
    const a = String.fromCharCode(65 + i);
    const b = String.fromCharCode(78 + i);
    return html`<tr><td class="id">${a}</td><td class="id">${ISO6346_LETTER_VALUES[a]}</td><td class="id">${b}</td><td class="id">${ISO6346_LETTER_VALUES[b]}</td></tr>`;
  }).join("") +
  `</tbody></table>`;

const ARTICLES: Article[] = [
  {
    id: "anatomy",
    title: "Anatomy of a container number",
    source: "BIC identification-number guide (bic-code.org/identification-number)",
    body: () => `
      <p>An ISO 6346 container number has four parts: an <b>owner prefix</b> of three letters identifying the owner or principal operator, an <b>equipment category identifier</b> (U for all freight containers, J for detachable freight-container-related equipment, Z for trailers and chassis), a <b>serial number</b> of six digits chosen by the owner, and one <b>check digit</b> that validates the recording and transmission of the owner code and serial number.</p>
      <p>The prefix holder, the owner, a lessor, the operator and the carrier moving the box today can all be different parties. A reefer is still category U; refrigeration belongs in the size/type code and the equipment attributes, not in the category letter.</p>
      <p>A correct check digit proves that the printed digit agrees with the body. It does not prove that the container exists, who owns or operates it, where it is, or that anyone may release it. A missing record in an external source is not evidence of nonexistence, and a registered prefix does not prove that a proposed serial number is right.</p>`,
  },
  {
    id: "arithmetic",
    title: "The check-digit arithmetic",
    source: "ISO 6346 as implemented in the kernel; the table below is generated from the values the checker computes with",
    body: () => `
      <p>Each of the first ten characters gets a numeric value: digits are themselves, letters run from A = 10 upward, skipping every multiple of 11 (11, 22, 33) so no letter value collides with the modulus. Position <i>i</i> (starting at 0) has weight 2<sup><i>i</i></sup>: 1, 2, 4, 8, 16, 32, 64, 128, 256, 512. The check digit is the weighted sum modulo 11, with remainder 10 written as 0.</p>
      ${letterTable()}
      <p>Worked example CSQU305438: C·1 + S·2 + Q·4 + U·8 + 3·16 + 0·32 + 5·64 + 4·128 + 3·256 + 8·512 = 6185; 6185 mod 11 = 3, so the number is CSQU3054383. Serials whose remainder is 10 validate with check digit 0; ISO 6346 advises owners to avoid issuing them because 0 then stands for two remainders.</p>
      <p><a href="/check#CSQU3054383">Open this example in the checker</a>.</p>`,
  },
  {
    id: "errors",
    title: "Common transcription errors, and the one the check digit cannot see",
    source: "kernel behaviour; error classes from the design of mod-11 check digits",
    body: () => `
      <p>The check digit catches every single-character error and most adjacent transpositions. It is blind to a substitution whose two characters have values that differ by a multiple of 11, because both contribute the same amount modulo 11. Digit 1 (value 1) and letter B (value 12) are the classic pair. Checkdigit never substitutes O for 0, I for 1 or B for 8 on its own; it shows the original and lets you decide.</p>
      <p>Try it: type a container number and see which single substitutions would keep the same check digit.</p>
      <div class="field-row"><label for="blind">Number</label><input id="blind" class="text-in" type="text" maxlength="12" value="MSKU1234565" autocapitalize="characters" spellcheck="false"></div>
      <div id="blind-out"></div>`,
  },
  {
    id: "sizetype",
    title: "Size/type codes",
    source: "ISO 6346 size and type tables as published by BIC, reproduced in the reference implementation; two values are marked unverified",
    body: () => `
      <p>The four-character size/type code (for example 22G1 or 45R1) describes the equipment: the first character is the length code, the second the height and width, the third and fourth the type. It is descriptive and is never a check-digit target. Length code 5 is not mapped because ISO marks it unassigned; a 45 ft unit uses L. Unknown or legacy codes decode as undefined rather than being guessed.</p>
      <div class="field-row"><label for="st">Size/type code</label><input id="st" class="text-in" type="text" maxlength="4" value="22G1" autocapitalize="characters" spellcheck="false"></div>
      <div id="st-out"></div>
      <p class="small muted">Unverified values: ${VERIFY_NOTES.map((n) => html`${n}`).join(" ")}</p>`,
  },
  {
    id: "ilu",
    title: "ILU codes (EN 13044)",
    source: "UIRR (uirr.com/services/ilu-code); normative EN 13044-1 text not verified",
    body: () => `
      <p>An ILU code identifies an intermodal loading unit: four letters (the fourth is A, B, D, E or K), six digits and one check digit. UIRR describes the ILU code as fully compatible with the BIC code used for maritime containers under ISO 6346 and refers to a defined calculation procedure for the check digit. Checkdigit routes categories A, B, D, E and K to the ILU set and checks them with the same mod-11 arithmetic as ISO 6346. That arithmetic has not been verified against the normative EN 13044-1 text, so this is a statement of compatibility, not of normative compliance.</p>`,
  },
  {
    id: "uic",
    title: "UIC wagon numbers",
    source: "kernel implementation of the Luhn mod-10 check on the first eleven digits",
    body: () => `
      <p>A twelve-digit UIC vehicle number carries its check digit last, computed by the Luhn mod-10 algorithm over the first eleven digits: weights alternate 2, 1, 2, 1 from the right, products of ten or more are digit-summed, and the check digit is (10 − sum mod 10) mod 10. The worked example 21 81 2471 217 gives 3.</p>
      <p>A bare twelve-digit run is not self-identifying. In a file, Checkdigit corrects a UIC number only in a field declared to carry rail vehicle numbers; elsewhere it is flagged even when the Luhn check passes. North American rail reporting marks are a different scheme and are not handled.</p>`,
  },
  {
    id: "registration",
    title: "Prefix registration and equipment records",
    source: "BIC (bic-code.org), BoxTech (bic-boxtech.org); linked, not queried",
    body: () => `
      <p>Owner prefixes are registered with the Bureau International des Containers. A registered prefix tells you who holds the code, not whether a particular serial exists. BoxTech is BIC's technical equipment database; a record there describes the unit's technical characteristics as reported by the owner. Neither is consulted by the public checker, and any future lookup would carry its source, timestamp and terms of use. BIC's API terms restrict copying and database creation.</p>
      <ul>
        <li>Register or look up a prefix: <a href="https://www.bic-code.org/" rel="noopener">bic-code.org</a></li>
        <li>BoxTech technical data: <a href="https://www.bic-boxtech.org/" rel="noopener">bic-boxtech.org</a></li>
      </ul>`,
  },
  {
    id: "codes",
    title: "Reference codes you will meet in terminal data",
    source: "UNECE, SMDG, NMFTA, IMO/SOLAS and UN/EDIFACT directories; descriptions only, no code lists bundled",
    body: () => `
      <table>
        <thead><tr><th>Code set</th><th>What it identifies</th><th>Authority</th></tr></thead>
        <tbody>
          <tr><td>UN/LOCODE</td><td>Ports and locations (for example NLRTM, USLAX)</td><td>UNECE; official releases are versioned, prerelease data is not the same thing</td></tr>
          <tr><td>Terminal and liner codes</td><td>Terminal facilities and carriers in maritime EDI</td><td>SMDG code lists</td></tr>
          <tr><td>SCAC</td><td>Carrier identity in North American transport documents</td><td>NMFTA, licensed</td></tr>
          <tr><td>Facility codes</td><td>Terminal or depot as used by a specific system or port community</td><td>The operating system or community; not globally unique</td></tr>
          <tr><td>EDIFACT message types</td><td>BAPLIE (bayplan), COPRAR (container discharge/load order), COARRI (arrival/departure), CODECO (gate in/out), COPARN (release/announcement), COREOR (release order), COPINO (pre-notification), COEDOR (stock report), MOVINS (stowage instruction), VERMAS (verified gross mass)</td><td>UN/EDIFACT with SMDG message implementation guides</td></tr>
          <tr><td>X12 transaction sets</td><td>310, 322, 404, 418 and related ocean, terminal and rail sets</td><td>ASC X12 with partner implementation guides</td></tr>
        </tbody>
      </table>`,
  },
  {
    id: "mass",
    title: "Mass, VGM, reefer and dangerous-goods fields",
    source: "SOLAS VI/2 VGM requirement; IMDG terminology; EDIFACT MEA/TMP/DGS segment usage in SMDG guides",
    body: () => `
      <p><b>Gross mass</b> is the declared total mass of the packed container; <b>verified gross mass (VGM)</b> is the mass obtained by one of the two SOLAS methods and declared by the shipper. They are different fields with different evidence and must not be merged. Units (kg versus lb) must be stated; a magnitude never implies a unit.</p>
      <p><b>Reefer</b> settings such as set temperature, unit and ventilation belong to the load, not to the equipment; a temperature without a unit or without the context of a specific movement is incomplete. <b>Dangerous goods</b> data carries UN number, class, packing group, flashpoint and related fields; completeness of those fields is a data-quality check, not an approval of stowage or segregation.</p>
      <p><b>CSC plate and ACEP</b> references describe the safety approval and continuous examination programme of the container structure. A number in a file is not proof that the plate is current.</p>`,
  },
  {
    id: "terminal-errors",
    title: "Common terminal data errors",
    source: "Checkdigit findings vocabulary; patterns observed across the sample files",
    body: () => `
      <ul>
        <li><b>Wrong check digit</b>: the digit was typed or transmitted wrongly; the body is usually right. Reported as an expected-check-digit proposal.</li>
        <li><b>Wrong body, plausible check digit</b>: a transposition inside the serial that the arithmetic cannot see, or a number copied from the wrong row. The check digit passes; only comparison with another source reveals it.</li>
        <li><b>Unknown category letter</b>: X or T in the fourth position is neither ISO 6346 nor ILU. Flagged, never corrected.</li>
        <li><b>Pseudo-prefixes</b>: owner codes with digits (L01U…) from synthetic or shipper-owned data. Flagged under the strict policy; corrected with standard arithmetic only under the lenient policy the user chose.</li>
        <li><b>Free-text hits</b>: a number in a remarks field may not be an equipment identifier at all. Flagged unless the field is declared.</li>
        <li><b>Missing X12 check element</b>: N7-18 absent means the digit cannot be written without restructuring the segment; flagged with the suggested digit.</li>
      </ul>`,
  },
  {
    id: "limits",
    title: "File modes and their limits",
    source: "this deployment's capability map (CAPABILITIES.md in the repository)",
    body: () => `
      <table>
        <thead><tr><th>Mode</th><th>Where it runs</th><th>Limits</th></tr></thead>
        <tbody>
          <tr><td>Check one number, check a list</td><td>This browser tab</td><td>Lists up to 10,000 numbers; nothing stored</td></tr>
          <tr><td>Files (local inspector)</td><td>A Web Worker in this tab</td><td>One file at a time. Up to 5 MiB: inspected whole in memory (plain text, delimited, fixed-width, EDIFACT, X12, container XML) with same-file near-miss suggestions. Above that and up to 5 GiB: streamed from disk (delimited, plain text, EDIFACT, X12, container XML; no fixed-width, no near-misses); proposals live in this site’s private browser storage until the job is discarded or the page is next opened. Excel workbooks not yet</td></tr>
          <tr><td>Private workspace</td><td>A configured service you control</td><td>Not part of this static site; larger files, durable jobs and history need it</td></tr>
        </tbody>
      </table>
      <p>Counts shown in the inspector are distinct quantities: physical lines, logical records (rows, segments or elements), identifier occurrences and unique identifiers.</p>`,
  },
  {
    id: "glossary",
    title: "Glossary",
    source: "Checkdigit vocabulary",
    body: () => `
      <table><tbody>
        <tr><td><b>Candidate</b></td><td>The identifier the arithmetic implies (body plus expected check digit). A proposal, never a confirmed identity.</td></tr>
        <tr><td><b>Flagged</b></td><td>Something needs a human: unknown category, non-standard prefix, free-text context or an operator deny rule.</td></tr>
        <tr><td><b>Import intent</b></td><td>The declared meaning of a file: comparison only, full snapshot, incremental update or explicit removal, with its scope and effective time.</td></tr>
        <tr><td><b>Mapping contract</b></td><td>The approved description of a feed's columns or paths, compared against what a file actually contains.</td></tr>
        <tr><td><b>Change set</b></td><td>The exact approved edits, identified by a fingerprint of their inputs. Approval binds to it and goes stale when inputs change.</td></tr>
        <tr><td><b>Surgical edit</b></td><td>Replacing only the check-digit characters in place; every other byte is preserved.</td></tr>
      </tbody></table>`,
  },
  {
    id: "keyboard",
    title: "Keyboard guidance and troubleshooting",
    source: "this site",
    body: () => `
      <ul>
        <li>Segmented input: type to advance, Backspace to go back, arrow keys to move, paste a whole number into any cell to replace all cells.</li>
        <li>Lists and files: Tab moves through controls; Enter or Space on a drop zone opens the file chooser; rows in the review table are focusable and Enter selects one.</li>
        <li>Nothing is remembered between visits. If a page shows an old result after an update, reload it.</li>
        <li>If a file is refused as unsupported, export it from its source system as CSV, EDIFACT, X12 or container XML; PDFs, images and Word documents are not scanned as text.</li>
      </ul>`,
  },
];

const nav = byId("ref-nav");
const container = byId("articles");
nav.innerHTML = ARTICLES.map((a, i) => html`<a href="#${a.id}"><span class="n">${i + 1}</span>${a.title}</a>`).join("");

// Compact topic selector for narrow viewports, where the sticky side nav is hidden.
const jump = byId<HTMLSelectElement>("ref-jump-select");
jump.innerHTML =
  `<option value="">Choose a topic…</option>` +
  ARTICLES.map((a, i) => html`<option value="${a.id}">${i + 1}. ${a.title}</option>`).join("");
jump.addEventListener("change", () => {
  const id = jump.value;
  if (!id) return;
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
});
container.innerHTML = ARTICLES.map(
  (a, i) => html`<article class="ref" id="${a.id}" data-title="${a.title}"><h2>${i + 1}. ${a.title}</h2><p class="meta">Source: ${a.source} · Reviewed ${REVIEWED}</p>${raw(a.body())}</article>`,
).join("");

// blind-spot demonstrator
function renderBlind(): void {
  const input = document.getElementById("blind") as HTMLInputElement | null;
  const out = document.getElementById("blind-out");
  if (!input || !out) return;
  const e = explain(input.value);
  if (!e.ok || e.kind !== "iso6346_ilu") {
    out.innerHTML = `<p class="muted small">Enter 4 letters and 6 or 7 digits.</p>`;
    return;
  }
  const hits: string[] = [];
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  for (let i = 0; i < 10; i++) {
    const ch = e.body[i] as string;
    const v = iso6346Value(ch);
    for (const alt of alphabet) {
      if (alt === ch) continue;
      if (i >= 4 && !/[0-9]/.test(alt)) continue;
      if (i < 4 && !/[A-Z]/.test(alt)) continue;
      const va = iso6346Value(alt);
      if ((va - v) % 11 === 0) hits.push(`${i + 1}: ${ch} ↔ ${alt}`);
    }
  }
  out.innerHTML = hits.length
    ? `<p class="small">Substitutions that keep check digit ${e.computed} unchanged (position: original ↔ alternative):</p><div class="blind-grid">${hits.map((h) => html`<span class="cell-pair hit">${h}</span>`).join("")}</div>`
    : `<p class="small">No single substitution in this number is invisible to the check digit.</p>`;
}
document.getElementById("blind")?.addEventListener("input", renderBlind);
renderBlind();

// size/type decoder
function renderSizeType(): void {
  const input = document.getElementById("st") as HTMLInputElement | null;
  const out = document.getElementById("st-out");
  if (!input || !out) return;
  const d = decodeSizeType(input.value);
  out.innerHTML =
    html`<p><span class="badge ${d.defined ? "passed" : "warning"}">${d.defined ? "defined" : "undefined"}</span> <span class="id">${d.code}</span></p>` +
    `<table><tbody>` +
    [
      ["Length", `${d.length_label} (${d.length_mm} mm)`],
      ["Height", `${d.height_label} (${d.height_mm} mm)`],
      ["Width", `${d.width_mm} mm${d.over_width ? ", pallet-wide" : ""}`],
      ["Type", d.type_label],
      ["Group", d.group],
      ["High cube", d.high_cube ? "yes" : "no"],
      ["TEU (drawing span)", String(d.teu)],
    ]
      .map(([k, v]) => html`<tr><td><b>${k}</b></td><td>${v}</td></tr>`)
      .join("") +
    `</tbody></table>` +
    (d.notes.length ? `<ul class="small">${d.notes.map((n) => html`<li>${n}</li>`).join("")}</ul>` : "");
}
document.getElementById("st")?.addEventListener("input", renderSizeType);
renderSizeType();

// quick checker (number or size/type code)
const quick = byId<HTMLInputElement>("quick");
const quickOut = byId("quick-out");
quick.addEventListener("input", () => {
  const v = quick.value.trim();
  if (!v) {
    quickOut.textContent = "";
    return;
  }
  if (/^[A-Za-z0-9]{4}$/.test(v) && !/^[A-Za-z]{4}$/.test(v)) {
    const d = decodeSizeType(v);
    quickOut.innerHTML = html`Size/type ${d.code}: ${d.length_label}, ${d.height_label}, ${d.type_label}${d.defined ? "" : " (undefined)"}`;
    return;
  }
  const t = validateToken(v);
  const check = t.layers.find((l) => l.layer === "check_digit");
  const structure = t.layers.find((l) => l.layer === "structure");
  quickOut.innerHTML = html`<span class="id">${t.normalized}</span> structure ${structure?.state}, check digit ${check?.state}${check?.detail ? `: ${check.detail}` : ""} — <a href="/check#${t.normalized}">open in the checker</a>`;
});

// search
const search = byId<HTMLInputElement>("search");
const count = byId("search-count");
const refEmpty = byId("ref-empty");
const refEmptyText = byId("ref-empty-text");
search.addEventListener("input", () => {
  const q = search.value.trim().toLowerCase();
  let shown = 0;
  for (const art of container.querySelectorAll<HTMLElement>("article.ref")) {
    const hit = !q || art.textContent?.toLowerCase().includes(q);
    art.hidden = !hit;
    if (hit) shown++;
  }
  count.textContent = q ? `${shown} of ${ARTICLES.length} articles` : "";
  if (q && shown === 0) {
    refEmptyText.textContent = `No articles match “${search.value.trim()}”. Clear the search to see all ${ARTICLES.length}.`;
    refEmpty.hidden = false;
  } else {
    refEmpty.hidden = true;
  }
});
