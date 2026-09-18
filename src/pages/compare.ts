import "../styles/site.css";
import "../styles/app.css";
import { compare, compareCsv, outcomeLabel, sideFromText, type CompareKey, type CompareResult } from "../lib/compare";
import { decodeBytes } from "../lib/encoding";
import { byId, html, raw } from "../ui/dom";

const el = {
  a: byId<HTMLTextAreaElement>("a"),
  b: byId<HTMLTextAreaElement>("b"),
  aLabel: byId<HTMLInputElement>("a-label"),
  bLabel: byId<HTMLInputElement>("b-label"),
  aFile: byId<HTMLInputElement>("a-file"),
  bFile: byId<HTMLInputElement>("b-file"),
  key: byId<HTMLSelectElement>("key"),
  keyNote: byId("key-note"),
  run: byId<HTMLButtonElement>("run"),
  csv: byId<HTMLAnchorElement>("dl-csv"),
  staleNote: byId("stale-note"),
  results: byId("results"),
  resultsSummary: byId("results-summary"),
  totals: byId("totals"),
  filter: byId<HTMLSelectElement>("filter"),
  table: byId<HTMLTableElement>("table"),
  empty: byId("empty"),
  emptyText: byId("empty-text"),
  thA: byId("th-a"),
  thB: byId("th-b"),
  optOnlyA: byId("opt-only-a"),
  optOnlyB: byId("opt-only-b"),
};

let result: CompareResult | null = null;
let url = "";

// Membership outcomes are neutral: the comparison reports where a number appears,
// it makes no validation judgement. A body-key run can also surface the same body
// carrying different check digits, which is a difference worth seeing.
const OUTCOME_DOT: Record<string, string> = { only_a: "", only_b: "", both: "in-both", conflict: "warn", duplicate: "" };

function keyNoteText(): string {
  return el.key.value === "body"
    ? "Two numbers match when their first ten characters agree, even if the check digit differs. The same body carrying different check digits is reported as a check-digit difference."
    : "Two numbers match only when every character agrees, check digit included.";
}

function render(): void {
  if (!result) return;
  const filter = el.filter.value;
  const shown = result.entries.filter((e) => filter === "all" || e.outcome === filter || (filter === "duplicate" && (e.a.length > 1 || e.b.length > 1)));
  const aL = el.aLabel.value.trim() || "First list";
  const bL = el.bLabel.value.trim() || "Second list";
  el.thA.textContent = aL;
  el.thB.textContent = bL;
  el.optOnlyA.textContent = `Only in ${aL}`;
  el.optOnlyB.textContent = `Only in ${bL}`;
  el.totals.innerHTML = [
    [`Only in ${aL}`, result.counts["only_a"]],
    [`Only in ${bL}`, result.counts["only_b"]],
    ["In both lists", result.counts["both"]],
    ["Check-digit differences", result.counts["conflict"]],
    ["Repeated within a list", result.counts["duplicate"]],
  ]
    .map(([k, v]) => html`<div class="tile"><span class="k">${String(k)}</span><span class="v">${String(v ?? 0)}</span></div>`)
    .join("");
  const body = el.table.tBodies[0] as HTMLTableSectionElement;
  body.innerHTML = shown
    .map(
      (e) => html`<tr>
        <td class="id">${e.key}</td>
        <td>${raw(badgeFor(e.outcome))} <span class="small">${outcomeLabel(e.outcome, aL, bL)}</span></td>
        <td class="id small">${e.a.map((r) => r.as_found).join(", ") || "—"}</td>
        <td class="id small">${e.b.map((r) => r.as_found).join(", ") || "—"}</td>
        <td class="small muted">${e.note}</td>
      </tr>`,
    )
    .join("");
  if (result.entries.length === 0) {
    el.emptyText.textContent = "No identifiers were found on either list.";
    el.empty.hidden = false;
  } else if (shown.length === 0) {
    el.emptyText.textContent = "No rows match this filter. Choose a different view above.";
    el.empty.hidden = false;
  } else {
    el.empty.hidden = true;
  }
  const total = result.entries.length;
  el.resultsSummary.textContent =
    `Comparison complete: ${total} distinct ${total === 1 ? "number" : "numbers"}. ` +
    `${result.counts["only_a"] ?? 0} only in ${aL}, ${result.counts["only_b"] ?? 0} only in ${bL}, ${result.counts["both"] ?? 0} in both.`;
  if (url) URL.revokeObjectURL(url);
  url = URL.createObjectURL(new Blob([compareCsv(result, aL, bL)], { type: "text/csv" }));
  el.csv.href = url;
  el.csv.hidden = false;
}

function badgeFor(outcome: string): string {
  const cls = OUTCOME_DOT[outcome] ?? "";
  return `<span class="dot ${cls}" aria-hidden="true"></span>`;
}

// Editing any input after a comparison exists makes the shown result and its
// export stale. Rather than leaving an outdated download available, the result
// is cleared and the user is told to run the comparison again.
function invalidate(): void {
  if (!result) return;
  result = null;
  el.results.hidden = true;
  el.csv.hidden = true;
  if (url) {
    URL.revokeObjectURL(url);
    url = "";
  }
  el.staleNote.hidden = false;
  el.staleNote.textContent = "Inputs changed. Run Compare again to refresh the results.";
}

el.run.addEventListener("click", () => {
  const a = sideFromText(el.aLabel.value.trim() || "First list", el.a.value);
  const b = sideFromText(el.bLabel.value.trim() || "Second list", el.b.value);
  result = compare(a, b, el.key.value as CompareKey);
  el.results.hidden = false;
  el.staleNote.hidden = true;
  el.staleNote.textContent = "";
  render();
});
el.filter.addEventListener("change", render);
el.key.addEventListener("change", () => {
  el.keyNote.textContent = keyNoteText();
  invalidate();
});
for (const control of [el.a, el.b, el.aLabel, el.bLabel]) {
  control.addEventListener("input", invalidate);
}

for (const [input, area] of [
  [el.aFile, el.a],
  [el.bFile, el.b],
] as const) {
  input.addEventListener("change", async () => {
    const f = input.files?.[0];
    if (!f) return;
    area.value = decodeBytes(new Uint8Array(await f.arrayBuffer())).text;
    invalidate();
  });
  area.addEventListener("dragover", (ev) => ev.preventDefault());
  area.addEventListener("drop", async (ev) => {
    const f = ev.dataTransfer?.files?.[0];
    if (!f) return;
    ev.preventDefault();
    area.value = decodeBytes(new Uint8Array(await f.arrayBuffer())).text;
    invalidate();
  });
}

el.keyNote.textContent = keyNoteText();
