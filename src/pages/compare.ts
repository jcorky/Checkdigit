import "../styles/site.css";
import "../styles/app.css";
import { compare, compareCsv, sideFromText, type CompareKey, type CompareResult } from "../lib/compare";
import { decodeBytes } from "../lib/encoding";
import { badge, byId, html, raw } from "../ui/dom";

const el = {
  a: byId<HTMLTextAreaElement>("a"),
  b: byId<HTMLTextAreaElement>("b"),
  aLabel: byId<HTMLInputElement>("a-label"),
  bLabel: byId<HTMLInputElement>("b-label"),
  aFile: byId<HTMLInputElement>("a-file"),
  bFile: byId<HTMLInputElement>("b-file"),
  key: byId<HTMLSelectElement>("key"),
  run: byId<HTMLButtonElement>("run"),
  csv: byId<HTMLAnchorElement>("dl-csv"),
  results: byId("results"),
  totals: byId("totals"),
  filter: byId<HTMLSelectElement>("filter"),
  table: byId<HTMLTableElement>("table"),
  empty: byId("empty"),
  thA: byId("th-a"),
  thB: byId("th-b"),
};

let result: CompareResult | null = null;
let url = "";

const OUTCOME_BADGE: Record<string, string> = { only_a: "warning", only_b: "pending", both: "passed", conflict: "failed", duplicate: "warning" };

function render(): void {
  if (!result) return;
  const filter = el.filter.value;
  const shown = result.entries.filter((e) => filter === "all" || e.outcome === filter || (filter === "duplicate" && (e.a.length > 1 || e.b.length > 1)));
  const aL = el.aLabel.value || "A";
  const bL = el.bLabel.value || "B";
  el.thA.textContent = aL;
  el.thB.textContent = bL;
  el.totals.innerHTML = [
    [`Only in ${aL}`, result.counts["only_a"]],
    [`Only in ${bL}`, result.counts["only_b"]],
    ["In both", result.counts["both"]],
    ["Conflicts", result.counts["conflict"]],
    ["Repeated within a side", result.counts["duplicate"]],
  ]
    .map(([k, v]) => html`<div class="tile"><span class="k">${String(k)}</span><span class="v">${String(v ?? 0)}</span></div>`)
    .join("");
  const body = el.table.tBodies[0] as HTMLTableSectionElement;
  el.empty.hidden = result.entries.length > 0;
  body.innerHTML = shown
    .map(
      (e) => html`<tr>
        <td class="id">${e.key}</td>
        <td>${raw(badge(OUTCOME_BADGE[e.outcome] ?? "neutral"))} <span class="small">${e.outcome.replace("_", " ")}</span></td>
        <td class="id small">${e.a.map((r) => r.as_found).join(", ") || "—"}</td>
        <td class="id small">${e.b.map((r) => r.as_found).join(", ") || "—"}</td>
        <td class="small muted">${e.note}</td>
      </tr>`,
    )
    .join("");
  if (url) URL.revokeObjectURL(url);
  url = URL.createObjectURL(new Blob([compareCsv(result, aL, bL)], { type: "text/csv" }));
  el.csv.href = url;
  el.csv.hidden = false;
}

el.run.addEventListener("click", () => {
  const a = sideFromText(el.aLabel.value || "A", el.a.value);
  const b = sideFromText(el.bLabel.value || "B", el.b.value);
  result = compare(a, b, el.key.value as CompareKey);
  el.results.hidden = false;
  render();
});
el.filter.addEventListener("change", render);

for (const [input, area] of [
  [el.aFile, el.a],
  [el.bFile, el.b],
] as const) {
  input.addEventListener("change", async () => {
    const f = input.files?.[0];
    if (!f) return;
    area.value = decodeBytes(new Uint8Array(await f.arrayBuffer())).text;
  });
  area.addEventListener("dragover", (ev) => ev.preventDefault());
  area.addEventListener("drop", async (ev) => {
    const f = ev.dataTransfer?.files?.[0];
    if (!f) return;
    ev.preventDefault();
    area.value = decodeBytes(new Uint8Array(await f.arrayBuffer())).text;
  });
}
