import "../styles/site.css";
import "../styles/app.css";
import { extractTokens, rowRecord, summarize, toCsv, toJson, type BulkRow } from "../lib/bulk";
import { decodeBytes } from "../lib/encoding";
import { badge, byId, html, raw } from "../ui/dom";

const el = {
  drop: byId("drop"),
  file: byId<HTMLInputElement>("file"),
  paste: byId<HTMLTextAreaElement>("paste"),
  run: byId<HTMLButtonElement>("run"),
  clear: byId<HTMLButtonElement>("clear"),
  note: byId("note"),
  results: byId("results"),
  totals: byId("totals"),
  filter: byId<HTMLSelectElement>("filter"),
  csv: byId<HTMLAnchorElement>("dl-csv"),
  json: byId<HTMLAnchorElement>("dl-json"),
  table: byId<HTMLTableElement>("table"),
  empty: byId("empty"),
};

let rows: BulkRow[] = [];
const urls: string[] = [];

function categoryOf(r: BulkRow): string {
  const structure = r.validation.layers.find((l) => l.layer === "structure")?.state;
  const check = r.validation.layers.find((l) => l.layer === "check_digit")?.state;
  if (structure === "failed") return "structure_failed";
  if (r.validation.kernel?.status === "flagged") return "flagged";
  if (check === "passed") return "check_passed";
  if (check === "failed") return "check_failed";
  if (check === "insufficient_information") return "computed";
  return "other";
}

function render(): void {
  const filter = el.filter.value;
  const shown = rows.filter((r) => filter === "all" || categoryOf(r) === filter || (filter === "flagged" && r.validation.kernel?.status === "flagged"));
  const s = summarize(rows);
  el.totals.innerHTML = [
    ["Numbers", s["total"]],
    ["Check passed", s["check_passed"]],
    ["Check failed", s["check_failed"]],
    ["Computed (no check digit)", s["computed"]],
    ["Flagged", s["flagged"]],
    ["Invalid structure", s["structure_failed"]],
  ]
    .map(([k, v]) => html`<div class="tile"><span class="k">${String(k)}</span><span class="v">${String(v)}</span></div>`)
    .join("");
  const body = el.table.tBodies[0] as HTMLTableSectionElement;
  el.empty.hidden = rows.length > 0;
  body.innerHTML = shown
    .map((r) => {
      const rec = rowRecord(r);
      const structure = r.validation.layers.find((l) => l.layer === "structure");
      const check = r.validation.layers.find((l) => l.layer === "check_digit");
      return html`<tr>
        <td class="muted small">${r.line}</td>
        <td class="id">${r.as_found}</td>
        <td class="id">${r.normalized}</td>
        <td>${rec.id_type}</td>
        <td>${raw(badge(structure?.state ?? "not_checked"))}</td>
        <td>${raw(badge(check?.state ?? "not_checked"))}${r.validation.kernel?.status === "flagged" ? raw(` ${badge("flagged")}`) : ""}</td>
        <td class="id">${rec.printed_check || "—"}</td>
        <td class="id">${rec.computed_check || "—"}</td>
        <td class="id">${rec.suggested || "—"}</td>
      </tr>`;
    })
    .join("");
  for (const u of urls.splice(0)) URL.revokeObjectURL(u);
  const csvUrl = URL.createObjectURL(new Blob([toCsv(shown)], { type: "text/csv" }));
  const jsonUrl = URL.createObjectURL(new Blob([toJson(shown)], { type: "application/json" }));
  urls.push(csvUrl, jsonUrl);
  el.csv.href = csvUrl;
  el.json.href = jsonUrl;
}

function run(text: string): void {
  const res = extractTokens(text);
  rows = res.rows;
  el.results.hidden = false;
  if (res.refused) {
    el.note.innerHTML = html`<span class="notice-error">Refused: the input contains about ${res.refused.count.toLocaleString()} numbers; the list checker handles ${res.refused.cap.toLocaleString()} at a time. Split the list or use the Files inspector.</span>`;
    rows = [];
    el.results.hidden = true;
    return;
  }
  el.note.textContent = rows.length ? `${rows.length.toLocaleString()} number${rows.length === 1 ? "" : "s"} found in ${res.cells_seen.toLocaleString()} cells.` : "No identifier-shaped tokens found.";
  render();
}

el.run.addEventListener("click", () => run(el.paste.value));
el.clear.addEventListener("click", () => {
  el.paste.value = "";
  rows = [];
  el.results.hidden = true;
  el.note.textContent = "";
});
el.filter.addEventListener("change", render);
el.drop.addEventListener("click", () => el.file.click());
el.drop.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    el.file.click();
  }
});
el.drop.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  el.drop.classList.add("over");
});
el.drop.addEventListener("dragleave", () => el.drop.classList.remove("over"));
async function useFile(f: File): Promise<void> {
  const { text } = decodeBytes(new Uint8Array(await f.arrayBuffer()));
  el.paste.value = text;
  run(text);
}
el.drop.addEventListener("drop", async (ev) => {
  ev.preventDefault();
  el.drop.classList.remove("over");
  const f = ev.dataTransfer?.files?.[0];
  if (f) await useFile(f);
});
el.file.addEventListener("change", async () => {
  const f = el.file.files?.[0];
  if (f) await useFile(f);
});
