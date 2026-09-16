import "../styles/site.css";
import { correctIdentifier, explain, FieldContext, Status } from "../lib/checkdigit";
import { byId, html, raw } from "../ui/dom";

// The segmented validator: eleven single-character inputs (owner 3, category 1,
// serial 6, check 1). The verdict comes from the kernel's decision table under
// the EQUIPMENT_ID context, exactly as a declared equipment field would be judged.

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
const PREFILL = "CSQU3054383";
const DEFAULT_HINT = "4 letters · 6 digits · 1 check";

const cellsEl = byId("cells");
const statusEl = byId("status");
const hintEl = byId("vhint");
const moreLink = byId<HTMLAnchorElement>("vmore-link");
const anatomy = {
  owner: byId("a-owner"),
  cat: byId("a-cat"),
  serial: byId("a-serial"),
  check: byId("a-check"),
};

const cells: HTMLInputElement[] = CELL_LABELS.map((label, i) => {
  if (i === 10) {
    const gap = document.createElement("div");
    gap.className = "gap";
    gap.setAttribute("aria-hidden", "true");
    cellsEl.appendChild(gap);
  }
  const input = document.createElement("input");
  input.type = "text";
  input.className = "cell" + (i === 10 ? " check" : "");
  input.maxLength = 11; // allow a paste to land in one cell; it is redistributed
  input.autocomplete = "off";
  input.spellcheck = false;
  input.inputMode = i < 4 ? "text" : "numeric";
  input.setAttribute("autocapitalize", "characters");
  input.setAttribute("aria-label", label);
  input.value = PREFILL[i] ?? "";
  cellsEl.appendChild(input);
  return input;
});

const clean = (s: string): string => s.toUpperCase().replace(/[^A-Z0-9]/g, "");

function setFrom(index: number, text: string): number {
  // Write characters into consecutive cells starting at `index`; return the
  // index of the cell after the last one written.
  let i = index;
  for (const ch of text) {
    if (i > 10) break;
    (cells[i] as HTMLInputElement).value = ch;
    i++;
  }
  return i;
}

cells.forEach((cell, i) => {
  cell.addEventListener("input", () => {
    const typed = clean(cell.value);
    cell.value = "";
    const next = setFrom(i, typed);
    if (typed.length > 0) cells[Math.min(next, 10)]?.focus();
    render();
  });
  cell.addEventListener("keydown", (ev) => {
    if (ev.key === "Backspace" && cell.value === "" && i > 0) {
      const prev = cells[i - 1] as HTMLInputElement;
      prev.value = "";
      prev.focus();
      ev.preventDefault();
      render();
    } else if (ev.key === "ArrowLeft" && i > 0) {
      cells[i - 1]?.focus();
      ev.preventDefault();
    } else if (ev.key === "ArrowRight" && i < 10) {
      cells[i + 1]?.focus();
      ev.preventDefault();
    }
  });
  cell.addEventListener("focus", () => cell.select());
});

function setCheckState(state: "" | "ok" | "fix" | "flag" | "bad" | "computed"): void {
  const check = cells[10] as HTMLInputElement;
  check.className = "cell check" + (state ? ` ${state}` : "");
}

function render(): void {
  const values = cells.map((c) => c.value);
  const token = values.join("");
  const firstEmpty = values.findIndex((v) => v === "");
  const contiguous = firstEmpty === -1 || values.slice(firstEmpty).every((v) => v === "");
  const dash = "—";

  anatomy.owner.textContent = token.slice(0, 3) || dash;
  anatomy.cat.textContent = values[3] || dash;
  anatomy.serial.textContent = values.slice(4, 10).join("") || dash;
  anatomy.check.textContent = dash;
  hintEl.textContent = DEFAULT_HINT;
  setCheckState("");
  moreLink.href = token.length >= 10 ? `/check#${token}` : "/check";

  if (token.length === 0) {
    statusEl.innerHTML = html`<span style="color:var(--faint)">e.g. CSQU3054383, MSKU1234565…</span>`;
    return;
  }
  if (!contiguous) {
    statusEl.innerHTML = html`<span class="t-flag">Fill the cells from left to right.</span>`;
    return;
  }
  if (token.length < 10) {
    const left = 10 - token.length;
    statusEl.innerHTML = html`<span class="t-flag">Keep going</span> — ${left} more character${left === 1 ? "" : "s"} to reach the check digit.`;
    return;
  }

  if (token.length === 10) {
    const e = explain(token);
    if (!e.ok || e.kind !== "iso6346_ilu") {
      setCheckState("bad");
      statusEl.innerHTML = html`<span class="t-bad">Invalid structure.</span> Expect 4 letters, then 6 digits, then 1 check digit.`;
      return;
    }
    anatomy.check.textContent = String(e.computed);
    (cells[10] as HTMLInputElement).placeholder = String(e.computed);
    setCheckState("computed");
    hintEl.textContent = "check digit computed";
    statusEl.innerHTML = html`Check digit is <span class="t-fix">${e.computed}</span>. Full number: <span class="num id">${e.full}</span>.${
      e.remainder_ten ? raw(' <span class="t-flag">Remainder 10 maps to 0.</span>') : ""
    }`;
    return;
  }

  const r = correctIdentifier(token, FieldContext.EQUIPMENT_ID);
  if (r.computed_check !== null) anatomy.check.textContent = r.computed_check;

  switch (r.status) {
    case Status.VALID:
      setCheckState("ok");
      hintEl.textContent = "valid";
      statusEl.innerHTML = html`<span class="t-ok">Valid.</span> The check digit is correct.`;
      break;
    case Status.CORRECTED:
      setCheckState("fix");
      hintEl.textContent = "needs correction";
      statusEl.innerHTML = html`<span class="t-fix">Check digit should be ${r.computed_check}.</span> The digit you typed (${r.printed_check}) is wrong: <span class="num id">${r.normalized}</span> → <span class="t-fix id">${r.corrected}</span>.`;
      break;
    case Status.FLAGGED:
      setCheckState("flag");
      hintEl.textContent = "flagged";
      statusEl.innerHTML = html`<span class="t-flag">Flagged.</span> ${r.reason}`;
      break;
    default:
      setCheckState("bad");
      hintEl.textContent = "invalid";
      statusEl.innerHTML = html`<span class="t-bad">Invalid structure.</span> ${r.reason}`;
  }
}

render();
