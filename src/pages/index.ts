import "../styles/site.css";
import {
  analyzeText,
  backspace,
  CELL_COUNT,
  cellsFrom,
  editCell,
  evaluate,
  replaceAll,
  tokenOf,
  type Cells,
  type EditResult,
  type Notice,
  type ViewModel,
} from "../lib/segmented";
import { byId, html, raw } from "../ui/dom";

// The segmented validator: eleven single-character inputs (owner 3, category 1,
// serial 6, check 1) kept in step with a whole-number field. All rules live in
// src/lib/segmented.ts; this file only binds them to the DOM.

const CELL_LABELS = [
  "Owner letter 1 of 3",
  "Owner letter 2 of 3",
  "Owner letter 3 of 3",
  "Category letter",
  "Serial digit 1 of 6",
  "Serial digit 2 of 6",
  "Serial digit 3 of 6",
  "Serial digit 4 of 6",
  "Serial digit 5 of 6",
  "Serial digit 6 of 6",
  "Check digit",
];
const EXAMPLE = "CSQU3054383";

const cellsEl = byId("cells");
const statusEl = byId("status");
const hintEl = byId("vhint");
const HINT_DEFAULT = hintEl.textContent ?? "";
const noticeEl = byId("notice");
const wholeEl = byId<HTMLInputElement>("whole");
const clearBtn = byId<HTMLButtonElement>("clear");
const exampleBtn = byId<HTMLButtonElement>("try-example");
const cta = byId<HTMLAnchorElement>("cta");
const anatomy = {
  owner: byId("a-owner"),
  cat: byId("a-cat"),
  serial: byId("a-serial"),
  check: byId("a-check"),
};

let cells: Cells = cellsFrom("");
let routed = false;

const cellInputs: HTMLInputElement[] = CELL_LABELS.map((label, i) => {
  if (i === 10) {
    const gap = document.createElement("div");
    gap.className = "gap";
    gap.setAttribute("aria-hidden", "true");
    cellsEl.appendChild(gap);
  }
  const input = document.createElement("input");
  input.type = "text";
  input.className = "cell" + (i === 10 ? " check" : "");
  input.maxLength = 32; // a paste lands here first and is redistributed or refused
  input.autocomplete = "off";
  input.spellcheck = false;
  input.inputMode = i < 4 ? "text" : "numeric";
  input.setAttribute("autocapitalize", "characters");
  input.setAttribute("aria-label", label);
  cellsEl.appendChild(input);
  return input;
});

function apply(result: EditResult): void {
  routed = false;
  cells = result.cells;
  showNotice(result.notice);
  render();
  if (result.focus !== null) cellInputs[result.focus]?.focus();
}

function showNotice(notice: Notice | null): void {
  if (!notice) {
    noticeEl.innerHTML = "";
    noticeEl.hidden = true;
    return;
  }
  noticeEl.hidden = false;
  const proposal = notice.proposal
    ? html` <button type="button" class="btn btn-ghost btn-small" id="use-proposal">Use <span class="id">${notice.proposal}</span></button>`
    : "";
  noticeEl.innerHTML = html`<span class="notice-${notice.level}">${notice.text}</span>${raw(proposal)}`;
  const btn = document.getElementById("use-proposal");
  if (btn && notice.proposal) {
    const proposal = notice.proposal;
    btn.addEventListener("click", () => apply(replaceAll(cells, proposal)));
  }
}

cellInputs.forEach((input, i) => {
  input.addEventListener("input", () => {
    const typed = input.value;
    input.value = cells[i] ?? "";
    apply(editCell(cells, i, typed));
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Backspace") {
      ev.preventDefault();
      const r = backspace(cells, i);
      cells = r.cells;
      showNotice(null);
      render();
      cellInputs[r.focus]?.focus();
    } else if (ev.key === "ArrowLeft" && i > 0) {
      ev.preventDefault();
      cellInputs[i - 1]?.focus();
    } else if (ev.key === "ArrowRight" && i < CELL_COUNT - 1) {
      ev.preventDefault();
      cellInputs[i + 1]?.focus();
    }
  });
  input.addEventListener("focus", () => input.select());
});

// The eleven-cell model holds a container or ILU number. A UIC wagon number
// (11 to 12 digits) or anything longer cannot fit; rather than reject it and
// leave a stale result on screen, the input is routed to the full checker.
function handleUnsupported(value: string): boolean {
  const norm = analyzeText(value).accepted;
  const isUic = /^[0-9]{11,12}$/.test(norm);
  if (!isUic && norm.length <= CELL_COUNT) return false;
  routed = true;
  cells = cellsFrom("");
  cellInputs.forEach((inp) => (inp.value = ""));
  const dash = "—";
  anatomy.owner.textContent = dash;
  anatomy.cat.textContent = dash;
  anatomy.serial.textContent = dash;
  anatomy.check.textContent = dash;
  hintEl.textContent = HINT_DEFAULT;
  setCheckState("");
  showNotice(null);
  if (isUic) {
    cta.href = `/check#uic:${norm}`;
    statusEl.innerHTML = html`<span class="t-flag">This looks like a UIC wagon number.</span> The quick checker here covers container and ILU numbers; open it in the full checker for the Luhn working. <a href="/check#uic:${norm}">Open it →</a>`;
  } else {
    cta.href = norm ? `/check#${norm}` : "/check";
    statusEl.innerHTML = html`<span class="t-flag">Too long for a container number.</span> Open it in the full checker. <a href="${norm ? `/check#${norm}` : "/check"}">Open it →</a>`;
  }
  return true;
}

wholeEl.addEventListener("input", () => {
  if (handleUnsupported(wholeEl.value)) return;
  routed = false;
  const r = replaceAll(cells, wholeEl.value);
  cells = r.cells;
  showNotice(r.notice);
  render(false);
});
wholeEl.addEventListener("change", () => {
  if (!routed) wholeEl.value = tokenOf(cells);
});
clearBtn.addEventListener("click", () => apply(replaceAll(cells, "")));
exampleBtn.addEventListener("click", () => apply(replaceAll(cells, EXAMPLE)));

function setCheckState(state: "" | "ok" | "fix" | "flag" | "bad" | "computed"): void {
  const check = cellInputs[10] as HTMLInputElement;
  check.className = "cell check" + (state ? ` ${state}` : "");
}

function render(syncWhole = true): void {
  const vm: ViewModel = evaluate(cells);
  cellInputs.forEach((input, i) => {
    input.value = cells[i] ?? "";
  });
  if (syncWhole) wholeEl.value = vm.token;
  const dash = "—";
  anatomy.owner.textContent = vm.anatomy.owner || dash;
  anatomy.cat.textContent = vm.anatomy.category || dash;
  anatomy.serial.textContent = vm.anatomy.serial || dash;
  anatomy.check.textContent = vm.anatomy.check || dash;
  hintEl.textContent = vm.token ? vm.hint : HINT_DEFAULT;
  (cellInputs[10] as HTMLInputElement).placeholder = vm.state === "computed" ? (vm.computedCheck ?? "") : "";
  cta.href = vm.token ? `/check#${vm.token}` : "/check";

  const r = vm.result;
  switch (vm.state) {
    case "empty":
      setCheckState("");
      statusEl.innerHTML = html`<span class="muted">e.g. CSQU3054383, MSKU1234565…</span>`;
      break;
    case "gap":
      setCheckState("");
      statusEl.innerHTML = html`<span class="t-flag">Fill the cells from left to right.</span>`;
      break;
    case "partial":
      setCheckState("");
      statusEl.innerHTML = html`<span class="t-flag">Keep going</span> — ${vm.missing} more character${vm.missing === 1 ? "" : "s"} to reach the check digit.`;
      break;
    case "computed":
      setCheckState("computed");
      statusEl.innerHTML = html`Check digit is <span class="t-fix">${vm.computedCheck}</span>. Full number: <span class="num id">${vm.token}${vm.computedCheck}</span>.${
        vm.explanation && vm.explanation.ok && vm.explanation.kind === "iso6346_ilu" && vm.explanation.remainder_ten
          ? raw(' <span class="t-flag">Remainder 10 maps to 0.</span>')
          : ""
      }`;
      break;
    case "valid":
      setCheckState("ok");
      statusEl.innerHTML = html`<span class="t-ok">Check digit agrees.</span> Structure and arithmetic pass; prefix registration and the equipment record are not checked here.`;
      break;
    case "corrected":
      setCheckState("fix");
      statusEl.innerHTML = html`<span class="t-fix">Expected check digit for this body: ${r?.computed_check}.</span> The digit you typed (${r?.printed_check}) does not agree: <span class="num id">${r?.normalized}</span> → <span class="t-fix id">${r?.corrected}</span>. If the body is wrong instead, the number is different.`;
      break;
    case "flagged":
      setCheckState("flag");
      statusEl.innerHTML = html`<span class="t-flag">Flagged.</span> ${r?.reason ?? ""}`;
      break;
    default:
      setCheckState("bad");
      statusEl.innerHTML = html`<span class="t-bad">Invalid structure.</span> ${r?.reason ?? "Expect 4 letters, then 6 digits, then 1 check digit."}`;
  }
}

showNotice(null);
render();
