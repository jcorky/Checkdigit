import "../styles/site.css";
import "../styles/app.css";
import { analyzeText } from "../lib/segmented";
import { buildResult, wireResultCopies, type ResultView, type Anatomy } from "../lib/result-view";
import { mathHtml, layersTable } from "../lib/explain-view";
import { type SchemeChoice } from "../lib/validation";
import { byId, html, raw } from "../ui/dom";
import { enhanceNav } from "../ui/nav";

// The home calculator. A single field, an explicit Check, and one of eight
// result states from the shared result view. The bulk tab mounts the same
// checker the /bulk page uses. All arithmetic runs here in the browser.

enhanceNav();

const EXAMPLE = "CSQU3054383";

const input = byId<HTMLInputElement>("calc-input");
const schemeEl = byId<HTMLSelectElement>("scheme");
const noticeEl = byId("notice");
const liveEl = byId("live");
const staleHint = byId("stale-hint");
const resultEl = byId("result");
const diagramEl = byId("diagram");
const howDis = byId<HTMLDetailsElement>("how-dis");
const howBody = byId("how-body");
const coversLayers = byId("covers-layers");
const fullWorking = byId<HTMLAnchorElement>("full-working");

const checkBtn = byId<HTMLButtonElement>("check-btn");
const clearBtn = byId<HTMLButtonElement>("clear-btn");
const exampleBtn = byId<HTMLButtonElement>("example-btn");

let checkedValue = "";

/* ---- identifier diagram (display only) ---- */
const DIAGRAM_LEGEND: Anatomy = { owner: "CSQ", category: "U", serial: "305438", check: "3", state: "none" };

function diagramHtml(a: Anatomy): string {
  const checkClass =
    a.state === "calc" ? " is-calc" : a.state === "ok" ? " is-ok" : a.state === "bad" ? " is-bad" : "";
  return html`<p class="diagram-caption">Anatomy of a container number</p>
    <div class="anatomy">
      <div class="apart"><span class="aval">${a.owner || "—"}</span><span class="alabel">Owner code<br><span class="anum">3 letters</span></span></div>
      <div class="apart"><span class="aval">${a.category || "—"}</span><span class="alabel">Category<br><span class="anum">U · J · Z</span></span></div>
      <div class="apart serial"><span class="aval">${a.serial || "—"}</span><span class="alabel">Serial number<br><span class="anum">6 digits</span></span></div>
      <div class="apart check${raw(checkClass)}"><span class="aval">${a.check || "—"}</span><span class="alabel">Check digit<br><span class="anum">mod 11</span></span></div>
    </div>`;
}

function renderDiagram(a: Anatomy | null): void {
  diagramEl.innerHTML = diagramHtml(a ?? DIAGRAM_LEGEND);
}

/* ---- normalization notice ---- */
function renderNotice(value: string): void {
  const n = analyzeText(value);
  if (n.rejected.length > 0) {
    noticeEl.hidden = false;
    const parts = n.rejected.map((r) => (r.count > 1 ? `"${r.char}" ×${r.count}` : `"${r.char}"`));
    const one = n.rejected.length === 1 && (n.rejected[0]?.count ?? 0) === 1;
    noticeEl.innerHTML = html`<span class="notice-error">${parts.join(", ")} ${one ? "is" : "are"} not an identifier character and ${one ? "was" : "were"} not removed. Original kept: "${value}".</span>`;
  } else if (n.separatorsRemoved > 0 || n.uppercased > 0) {
    noticeEl.hidden = false;
    const bits: string[] = [];
    if (n.separatorsRemoved > 0) bits.push(`removed ${n.separatorsRemoved} separator${n.separatorsRemoved === 1 ? "" : "s"}`);
    if (n.uppercased > 0) bits.push(`upper-cased ${n.uppercased} letter${n.uppercased === 1 ? "" : "s"}`);
    noticeEl.innerHTML = html`<span class="notice-info">Read "${value}" as ${n.accepted} (${bits.join(", ")}).</span>`;
  } else {
    noticeEl.hidden = true;
    noticeEl.innerHTML = "";
  }
}

/* ---- run a check ---- */
function runCheck(): void {
  const value = input.value;
  const scheme = schemeEl.value as SchemeChoice;
  renderNotice(value);
  const view: ResultView = buildResult(value, scheme);

  resultEl.dataset["outdated"] = "false";
  staleHint.hidden = true;
  resultEl.innerHTML = view.cardHtml;
  liveEl.textContent = view.announce;
  wireResultCopies(resultEl, (msg) => (liveEl.textContent = msg));

  renderDiagram(view.anatomy);

  const math = mathHtml(view.explanation);
  if (math) {
    howBody.innerHTML = math;
    howDis.hidden = false;
  } else {
    howDis.hidden = true;
    howDis.open = false;
  }

  coversLayers.innerHTML = view.validation ? layersTable(view.validation) : "";
  fullWorking.href = view.normalized ? `/check#${view.normalized}` : "/check";
  checkedValue = value;
}

function clearAll(): void {
  input.value = "";
  schemeEl.value = "auto";
  renderNotice("");
  resultEl.innerHTML = buildResult("", "auto").cardHtml;
  resultEl.dataset["outdated"] = "false";
  staleHint.hidden = true;
  renderDiagram(null);
  howDis.hidden = true;
  howDis.open = false;
  coversLayers.innerHTML = "";
  fullWorking.href = "/check";
  liveEl.textContent = "Cleared.";
  checkedValue = "";
  input.focus();
}

function markOutdated(): void {
  const shown = resultEl.querySelector(".result-card");
  if (shown && input.value !== checkedValue) {
    resultEl.dataset["outdated"] = "true";
    staleHint.hidden = false;
  } else {
    resultEl.dataset["outdated"] = "false";
    staleHint.hidden = true;
  }
}

checkBtn.addEventListener("click", runCheck);
clearBtn.addEventListener("click", clearAll);
exampleBtn.addEventListener("click", () => {
  input.value = EXAMPLE;
  runCheck();
  input.focus();
});
input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    runCheck();
  }
});
input.addEventListener("input", markOutdated);
schemeEl.addEventListener("change", () => {
  // A scheme change re-checks immediately if a result is already shown.
  if (resultEl.querySelector(".result-card") && checkedValue) runCheck();
});

/* ---- tabs ---- */
const tabSingle = byId<HTMLButtonElement>("tab-single");
const tabBulk = byId<HTMLButtonElement>("tab-bulk");
const panelSingle = byId("panel-single");
const panelBulk = byId("panel-bulk");
let bulkMounted = false;

// The bulk checker (and its file decoding) load on demand, so the single-number
// path stays small on first paint.
async function ensureBulk(): Promise<void> {
  if (bulkMounted) return;
  bulkMounted = true;
  const mod = await import("../lib/bulk-view");
  panelBulk.innerHTML = mod.BULK_PANEL_HTML;
  mod.mountBulk();
}

function selectTab(which: "single" | "bulk", focus = false): void {
  const single = which === "single";
  tabSingle.setAttribute("aria-selected", single ? "true" : "false");
  tabBulk.setAttribute("aria-selected", single ? "false" : "true");
  tabSingle.tabIndex = single ? 0 : -1;
  tabBulk.tabIndex = single ? -1 : 0;
  panelSingle.hidden = !single;
  panelBulk.hidden = single;
  if (!single) void ensureBulk();
  if (focus) (single ? tabSingle : tabBulk).focus();
}

tabSingle.addEventListener("click", () => selectTab("single"));
tabBulk.addEventListener("click", () => selectTab("bulk"));
for (const tab of [tabSingle, tabBulk]) {
  tab.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
      ev.preventDefault();
      selectTab(tab === tabSingle ? "bulk" : "single", true);
    }
  });
}

/* ---- initial paint ---- */
renderNotice("");
resultEl.innerHTML = buildResult("", "auto").cardHtml;
resultEl.dataset["outdated"] = "false";
renderDiagram(null);
