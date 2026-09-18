import "../styles/site.css";
import { normalize } from "../lib/checkdigit";
import { analyzeText } from "../lib/segmented";
import { buildResult, wireResultCopies } from "../lib/result-view";
import { mathHtml, layersTable, summaryText } from "../lib/explain-view";
import { type SchemeChoice } from "../lib/validation";
import { byId, html, raw } from "../ui/dom";
import { enhanceNav } from "../ui/nav";

// The detailed single-number page: the same result card as the home
// calculator, plus the worked arithmetic, every validation layer, and a
// shareable permalink that carries the number and the chosen scheme.

enhanceNav();

const input = byId<HTMLInputElement>("tok");
const schemeEl = byId<HTMLSelectElement>("scheme");
const noticeEl = byId("notice");
const liveEl = byId("live");
const out = byId("out");
const checkBtn = byId<HTMLButtonElement>("check-btn");
const clearBtn = byId<HTMLButtonElement>("clear-btn");
const exampleBtn = byId<HTMLButtonElement>("example-btn");

const EXAMPLE = "CSQU3054383";
const SCHEMES = new Set(["iso6346", "ilu", "uic"]);

// The fragment carries the selected scheme so a shared check reopens under the
// same conditions: "#iso6346:CSQU3054384", or just "#CSQU3054384" for auto.
function fragmentFor(norm: string, scheme: string): string {
  return scheme !== "auto" && SCHEMES.has(scheme) ? `${scheme}:${norm}` : norm;
}

function shareUrl(norm: string): string {
  return `${location.origin}/check#${fragmentFor(norm, schemeEl.value)}`;
}

const say = (msg: string): void => {
  liveEl.textContent = msg;
};

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

function render(): void {
  const value = input.value;
  const scheme = schemeEl.value as SchemeChoice;
  const view = buildResult(value, scheme);
  const norm = normalize(value);

  history.replaceState(null, "", norm ? `#${fragmentFor(norm, scheme)}` : location.pathname);
  renderNotice(value);

  if (view.kind === "empty") {
    out.innerHTML = view.cardHtml;
    liveEl.textContent = "";
    return;
  }

  const math = mathHtml(view.explanation);
  const detail = view.validation
    ? html`<details class="explain"><summary>Show the arithmetic and every validation layer</summary><div class="explain-body">${raw(math)}${raw(layersTable(view.validation))}</div></details>`
    : "";
  const share = html`<div class="share">
      <span>Permalink</span> <code id="share-url">${shareUrl(norm)}</code>
      <button class="btn btn-ghost btn-small" type="button" id="copy">Copy link</button>
      <button class="btn btn-ghost btn-small" type="button" id="copy-result">Copy result as text</button>
    </div>`;

  out.innerHTML = view.cardHtml + detail + share;
  liveEl.textContent = view.announce;
  wireResultCopies(out, say);

  if (view.validation) {
    const v = view.validation;
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
}

function clearAll(): void {
  input.value = "";
  schemeEl.value = "auto";
  renderNotice("");
  out.innerHTML = buildResult("", "auto").cardHtml;
  history.replaceState(null, "", location.pathname);
  say("Cleared.");
  input.focus();
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

checkBtn.addEventListener("click", render);
clearBtn.addEventListener("click", clearAll);
exampleBtn.addEventListener("click", () => {
  input.value = EXAMPLE;
  render();
  input.focus();
});
input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    render();
  }
});
schemeEl.addEventListener("change", () => {
  if (input.value) render();
});
window.addEventListener("hashchange", () => {
  fromHash();
  render();
});

fromHash();
if (input.value) render();
else out.innerHTML = buildResult("", "auto").cardHtml;
