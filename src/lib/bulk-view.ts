import { decodeBytes } from "./encoding";
import {
  rowRecord,
  summarize,
  toCsv,
  toTsv,
  validateEntries,
  type BulkRow,
} from "./bulk";
import { byId, html, raw } from "../ui/dom";

/*
 * The simple bulk checker, shared by the home "Bulk check" tab and the /bulk
 * page so the two never diverge. Every non-empty entry gets one row; the
 * original text, order and duplicates are preserved; the summary reconciles
 * with the entries (matches + mismatches + calculated + invalid = total).
 */

type Bucket = "matches" | "mismatches" | "calculated" | "invalid";

export const BULK_PANEL_HTML = `
<div class="surface">
  <label class="calc-label" for="bulk-input">Numbers to check</label>
  <textarea id="bulk-input" class="text-area" rows="8" spellcheck="false"
    placeholder="One number per line:&#10;CSQU3054383&#10;MSKU1234565&#10;APLU100000"
    aria-describedby="bulk-help"></textarea>
  <p class="calc-help" id="bulk-help">One number per line. Commas and tabs also separate entries; spaces and hyphens inside a single number are kept and normalised away. Ten characters calculates the check digit; eleven checks a complete number.</p>
  <div class="calc-actions">
    <button type="button" class="btn btn-primary" id="bulk-run">Check numbers</button>
    <button type="button" class="btn btn-ghost" id="bulk-clear">Clear</button>
    <label class="btn btn-quiet btn-small" for="bulk-file">Load a .txt or .csv file
      <input id="bulk-file" type="file" class="sr-only" accept=".txt,.csv,.tsv,text/plain,text/csv">
    </label>
    <span class="muted small" id="bulk-note" role="status" aria-live="polite"></span>
  </div>
</div>
<div id="bulk-results" class="result" hidden>
  <div id="bulk-summary" class="summary-tiles" role="group" aria-label="Result summary; select a tile to filter"></div>
  <p class="muted small" id="bulk-flagged" hidden></p>
  <div class="bulk-toolbar">
    <span class="muted small" id="bulk-scope"></span>
    <span class="spacer"></span>
    <button type="button" class="btn btn-ghost btn-small" id="bulk-copy">Copy results</button>
    <a class="btn btn-ghost btn-small" id="bulk-csv" download="checkdigit-list.csv">Download CSV</a>
  </div>
  <p class="sr-only" id="bulk-live" role="status" aria-live="polite"></p>
  <div class="tablewrap">
    <table class="results" id="bulk-table">
      <thead><tr>
        <th scope="col">Line</th><th scope="col">Entered</th><th scope="col">Normalised</th><th scope="col">Type</th>
        <th scope="col">Result</th><th scope="col">Printed</th><th scope="col">Expected</th><th scope="col">Suggested</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="bulk-cards" id="bulk-cardlist"></div>
  <p class="empty-state" id="bulk-empty" hidden><span class="stripes" aria-hidden="true"></span><span id="bulk-empty-text"></span></p>
</div>`;

const SCHEME_SHORT: Record<string, string> = {
  iso6346: "ISO 6346",
  ilu: "ILU",
  uic: "UIC",
  unknown: "—",
};

function bucketOf(r: BulkRow): Bucket {
  const structure = r.validation.layers.find((l) => l.layer === "structure")?.state;
  const check = r.validation.layers.find((l) => l.layer === "check_digit")?.state;
  if (structure === "failed") return "invalid";
  if (check === "passed") return "matches";
  if (check === "failed") return "mismatches";
  if (check === "insufficient_information") return "calculated";
  return "invalid";
}

const isFlagged = (r: BulkRow): boolean => r.validation.kernel?.status === "flagged";

const RESULT_META: Record<Bucket, { label: string; dot: string; card: string }> = {
  matches: { label: "Match", dot: "ok", card: "is-ok" },
  mismatches: { label: "Mismatch", dot: "bad", card: "is-bad" },
  calculated: { label: "Calculated", dot: "calc", card: "is-calc" },
  invalid: { label: "Invalid", dot: "bad", card: "is-bad" },
};

function suggestedFor(r: BulkRow, bucket: Bucket): string {
  const rec = rowRecord(r);
  if (bucket === "calculated") return rec.suggested; // completed number
  if (bucket === "mismatches") return rec.suggested; // conditional suggestion
  return ""; // matches and invalid: never present a candidate as confirmed
}

export function mountBulk(): void {
  const input = byId<HTMLTextAreaElement>("bulk-input");
  const runBtn = byId<HTMLButtonElement>("bulk-run");
  const clearBtn = byId<HTMLButtonElement>("bulk-clear");
  const fileInput = byId<HTMLInputElement>("bulk-file");
  const note = byId("bulk-note");
  const results = byId("bulk-results");
  const summary = byId("bulk-summary");
  const flaggedNote = byId("bulk-flagged");
  const scope = byId("bulk-scope");
  const copyBtn = byId<HTMLButtonElement>("bulk-copy");
  const csvLink = byId<HTMLAnchorElement>("bulk-csv");
  const live = byId("bulk-live");
  const tbody = byId<HTMLTableElement>("bulk-table").tBodies[0] as HTMLTableSectionElement;
  const cards = byId("bulk-cardlist");
  const empty = byId("bulk-empty");
  const emptyText = byId("bulk-empty-text");

  let rows: BulkRow[] = [];
  let filter: "all" | Bucket | "flagged" = "all";
  const urls: string[] = [];

  const say = (msg: string): void => {
    live.textContent = msg;
  };

  function shownRows(): BulkRow[] {
    if (filter === "all") return rows;
    if (filter === "flagged") return rows.filter(isFlagged);
    return rows.filter((r) => bucketOf(r) === filter);
  }

  function renderSummary(): void {
    const s = summarize(rows);
    const tiles: [string, string, number, string][] = [
      ["all", "Numbers", s["total"] ?? 0, ""],
      ["matches", "Matches", s["check_passed"] ?? 0, "is-ok"],
      ["mismatches", "Mismatches", s["check_failed"] ?? 0, "is-bad"],
      ["calculated", "Calculated", s["computed"] ?? 0, "is-calc"],
      ["invalid", "Invalid", s["structure_failed"] ?? 0, "is-bad"],
    ];
    summary.innerHTML = tiles
      .map(
        ([key, label, value, cls]) =>
          html`<button type="button" class="tile ${cls}${filter === key ? " on" : ""}" data-filter="${key}" aria-pressed="${filter === key ? "true" : "false"}"><span class="k">${label}</span><span class="v">${String(value)}</span></button>`,
      )
      .join("");
    for (const btn of summary.querySelectorAll<HTMLButtonElement>(".tile")) {
      btn.addEventListener("click", () => {
        filter = btn.dataset["filter"] as typeof filter;
        render();
      });
    }
    const flagged = s["flagged"] ?? 0;
    if (flagged > 0) {
      flaggedNote.hidden = false;
      flaggedNote.innerHTML = html`<button type="button" class="linklike" id="bulk-flag-btn">${String(flagged)} entr${flagged === 1 ? "y" : "ies"} flagged for review</button> — a non-standard prefix, an unknown category, or a bare UIC number. Shown with a review mark.`;
      byId("bulk-flag-btn").addEventListener("click", () => {
        filter = "flagged";
        render();
      });
    } else {
      flaggedNote.hidden = true;
    }
  }

  function rowCells(r: BulkRow): { rec: ReturnType<typeof rowRecord>; bucket: Bucket; meta: (typeof RESULT_META)[Bucket]; suggested: string } {
    const rec = rowRecord(r);
    const bucket = bucketOf(r);
    return { rec, bucket, meta: RESULT_META[bucket], suggested: suggestedFor(r, bucket) };
  }

  function render(): void {
    renderSummary();
    const shown = shownRows();

    tbody.innerHTML = shown
      .map((r) => {
        const { rec, meta, suggested } = rowCells(r);
        const flag = isFlagged(r) ? raw(` <span class="badge flagged">Review</span>`) : "";
        return html`<tr>
          <td class="num">${r.line}</td>
          <td class="id">${r.as_found}</td>
          <td class="id">${r.normalized || "—"}</td>
          <td>${SCHEME_SHORT[rec.id_type] ?? rec.id_type}</td>
          <td><span class="status-cell"><span class="dotmark ${meta.dot}" aria-hidden="true"></span>${meta.label}${flag}</span></td>
          <td class="id">${rec.printed_check || "—"}</td>
          <td class="id">${rec.computed_check || "—"}</td>
          <td class="id">${suggested || "—"}</td>
        </tr>`;
      })
      .join("");

    cards.innerHTML = shown
      .map((r) => {
        const { rec, meta, suggested } = rowCells(r);
        const flag = isFlagged(r) ? raw(` <span class="badge flagged">Review</span>`) : "";
        const kv: [string, string][] = [
          ["Normalised", r.normalized || "—"],
          ["Type", SCHEME_SHORT[rec.id_type] ?? rec.id_type],
          ["Printed", rec.printed_check || "—"],
          ["Expected", rec.computed_check || "—"],
        ];
        if (suggested) kv.push(["Suggested", suggested]);
        return html`<div class="bulk-card ${meta.card}">
          <div class="bc-top">
            <span class="bc-id">${r.as_found}</span>
            <span class="status-cell"><span class="dotmark ${meta.dot}" aria-hidden="true"></span>${meta.label}${flag}</span>
          </div>
          <dl class="bc-kv">${raw(kv.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`).join(""))}</dl>
          <span class="bc-line">line ${r.line}</span>
        </div>`;
      })
      .join("");

    if (rows.length === 0) {
      emptyText.textContent = "No numbers to check. Paste a list above and choose Check numbers.";
      empty.hidden = false;
    } else if (shown.length === 0) {
      emptyText.textContent = "No entries match this view. Choose a different tile above.";
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }

    const allScope = filter === "all";
    scope.textContent = allScope
      ? `${rows.length.toLocaleString()} entr${rows.length === 1 ? "y" : "ies"}.`
      : `Showing ${shown.length.toLocaleString()} of ${rows.length.toLocaleString()}. Export and copy use this view.`;

    for (const u of urls.splice(0)) URL.revokeObjectURL(u);
    const csvUrl = URL.createObjectURL(new Blob([toCsv(shown)], { type: "text/csv" }));
    urls.push(csvUrl);
    csvLink.href = csvUrl;
    csvLink.download = allScope ? "checkdigit-list.csv" : "checkdigit-list-filtered.csv";
    csvLink.textContent = allScope
      ? `Download CSV (${shown.length.toLocaleString()})`
      : `Download ${shown.length.toLocaleString()} shown (CSV)`;
    copyBtn.textContent = allScope ? "Copy results" : `Copy ${shown.length.toLocaleString()} shown`;
  }

  function run(text: string): void {
    const res = validateEntries(text);
    if (res.refused) {
      results.hidden = true;
      note.innerHTML = html`<span class="notice-error">Too many entries: about ${res.refused.count.toLocaleString()} were supplied and this checker handles ${res.refused.cap.toLocaleString()} at a time. Split the list, or use <a href="/files">Review a file</a>.</span>`;
      rows = [];
      return;
    }
    rows = res.rows;
    filter = "all";
    results.hidden = false;
    note.textContent = rows.length
      ? `${rows.length.toLocaleString()} entr${rows.length === 1 ? "y" : "ies"} checked.`
      : "No numbers found.";
    render();
    say(`${rows.length} entries checked.`);
  }

  runBtn.addEventListener("click", () => run(input.value));
  input.addEventListener("keydown", (ev) => {
    // Ctrl/Cmd+Enter submits; a plain Enter stays a newline in the textarea.
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      run(input.value);
    }
  });
  clearBtn.addEventListener("click", () => {
    input.value = "";
    rows = [];
    results.hidden = true;
    note.textContent = "";
    input.focus();
  });
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(toTsv(shownRows()));
      say("Results copied. Paste into a spreadsheet.");
      const t = copyBtn.textContent;
      copyBtn.textContent = "Copied";
      window.setTimeout(() => (copyBtn.textContent = t), 1400);
    } catch {
      say("Copy failed — use Download CSV instead.");
    }
  });
  async function useFile(f: File): Promise<void> {
    const { text } = decodeBytes(new Uint8Array(await f.arrayBuffer()));
    input.value = text;
    run(text);
  }
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files?.[0];
    if (f) await useFile(f);
    fileInput.value = "";
  });
}
