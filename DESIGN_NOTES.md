# Redesign: calculator-led, light industrial utility

This note records the design decisions behind the public checker's redesign and the
verification that was run. The goal was to make one workflow effortless — *enter or
paste a number → calculate or check → understand the result → copy or export* — while
preserving every existing capability (ISO 6346 / ILU / UIC calculation, file
inspection, list comparison, the reference library) and the privacy architecture.

## Final design decisions

**Lead with the calculator.** The home page (`/`, the "Calculator") opens on the task,
not a slogan: an `<h1>` of *Container Check Digit Calculator*, one sentence of support,
and immediately a **Single number / Bulk check** tab pair. The old decorative hero, the
eleven editable character boxes, and the prefilled success state are gone.

**One field, not two editors.** Single-number mode is one labelled, paste-friendly
field (*Container number*). The old "whole number input + 11 character cells" pair —
which duplicated the same value and could read as a form already completed — is
replaced by a single input plus a **display-only** identifier diagram
(`CSQ · U · 305438 · [3]`) that labels the owner code, category, serial and check
digit, with the check digit in a square outline that echoes a container's stencilled
markings. The diagram explains the number; it never becomes a second place to type.

**Explicit submit.** The calculator computes on **Check number** or the Enter key, not
on every keystroke, so an incomplete entry never flashes an alarming error. When the
number changes after a result is shown, the old result is dimmed and marked *press
Check number to update*. **Clear** empties the field and the result and returns focus.

**One action colour, four status colours.** The palette is the one in the brief: page
`#F4F6F8`, surfaces `#FFFFFF`, primary text `#172B3A`, secondary `#526171`, a single
action blue `#1459B8`, and status colours that are always paired with a word and an
icon — match green `#146C43`, mismatch/error red `#B42318`, review amber `#8A4B08`,
neutral slate. Blue never means "valid"; green never means "go here". Every text/surface
pair was checked at ≥4.5:1 and the input boundary at ≥3:1 (see below).

**Result states.** Eight distinct states — empty, incomplete, calculated, matching,
mismatched, unsupported-category, invalid-format, and a copy-failure fallback — each
with its own copy:
- *Calculated* shows the digit at 40–48px, the completed number, and **Copy full number**.
- *Matching* leads with "Check digit matches." and the full number.
- *Mismatched* keeps the entered number and the suggestion strictly separate:
  **Entered digit**, **Expected digit**, and *If the first ten characters are correct: …*
  with **Copy suggested number** and the caveat that the suggestion is not a confirmed
  correction — the error may be in the first ten characters.
- *Invalid* explains the specific problem and never fabricates a valid-looking
  identifier; a letter sitting where a digit belongs (e.g. `O` vs `0`) is called out and
  never silently substituted.

Beside every positive result: *"This checks the format and check digit. It does not
confirm that a container exists or is registered."* The worked arithmetic and the
per-layer validation live in two collapsed disclosures — **How this was calculated** and
**What this check covers** — so they never crowd the answer. Internal rule names and
parser versions stay out of the primary flow.

**Bulk check, brought to the surface.** Simple bulk checking used to be buried inside the
file inspector. It is now a first-class tab on the home page and a standalone `/bulk`
page (both mount the same component). One number per line; commas and tabs also separate;
spaces and hyphens inside a number are kept and normalised away. **Every** non-empty
entry gets a row — including malformed and incomplete ones — the summary counts reconcile
with the entries (matches + mismatches + calculated + invalid = total), and **Copy
results** / **Download CSV** export the complete report with the original values; a filter
narrows both the view and the export, stated explicitly. A results table on desktop
becomes labelled cards on mobile, with no page-wide horizontal scroll.

**Navigation.** A compact header — *Checkdigit · Calculator · Tools · Reference* — with
**Tools** a keyboard-operable disclosure that exposes Bulk check, Review a file and
Compare lists. ISO 6346 is the default; ILU and UIC are reachable through an *Other
identifier types* control with scheme-specific guidance.

**Typography.** IBM Plex Sans for the interface and IBM Plex Mono for identifiers (which
distinguish `0/O` and `1/I`), self-hosted under `/fonts/` with system fallbacks, so the
page still makes no outside request.

**Light only.** The interface is deliberately light across the board — a precise, quiet
utility for someone checking numbers all day — rather than following the OS dark
preference. `color-scheme: light` keeps native controls consistent.

## What was preserved

The ISO 6346 / ILU / UIC kernel (`src/lib/checkdigit.ts`, the `CALC-PURE` block) is
unchanged and reused everywhere; parity against the Python reference still holds. The
Files inspector (format detection, column mapping, reviewed corrections, exports), the
Compare tool, and the Reference library with the size/type decoder are intact and
restyled, not simplified. Existing URLs — including the `/check#CSQU3054384` permalink and
scheme-prefixed fragments like `#uic:…` — still work. Nothing is uploaded, stored,
logged, or sent to any third party; owner-code lookup remains an explicit link to BIC.

## Verification record

Automated (all passing):
- **358 vitest tests** across 18 files, including the ported kernel parity vectors, the
  built-chunk parity, the new `tests/acceptance.test.ts` (the brief's twelve single-number
  fixtures, the four-entry batch producing exactly four outcomes, and the CSV
  formula-injection guard), and updated interface-state guards.
- **`site.test.ts`**: one HTML file per route; **no external scripts/styles/images/fonts/
  frames**; no network calls; first-load JS for `/` under 60 kB gzipped (measured ≈12 kB);
  a single kernel chunk reproducing every vector; the new palette's contrast (text ≥4.5:1
  on every surface, status text ≥4.5:1 on its tint, white ≥4.5:1 on the action colour,
  input boundary ≥3:1); and every route served 200 by `wrangler dev`, unknown paths 404.
- **Python suites + `run_acceptance.py`**: `ACCEPTANCE PASS`, kernel/substitution/contract
  and workspace suites green — the calculation source of truth is unchanged.

Browser verification (Chromium via the in-app browser, `wrangler dev`):
- States inspected: empty, calculated (`CSQU305438` → 3 → `CSQU3054383`), matching,
  mismatched (`CSQU3054384` → entered 4 / expected 3 / suggested `CSQU3054383`), invalid
  (`CSQU3O54383` — no zero-substitution, no fabricated number), unsupported category, and
  the visible normalisation notice for `csqu 305438 3`.
- Behaviour: Enter submits; Clear empties and refocuses; a changed number marks the result
  outdated; the *How this was calculated* and *What this check covers* disclosures populate;
  the diagram reflects the entered number.
- Bulk: the mixed six-entry batch summarised 3 matches / 1 mismatch / 1 calculated /
  1 invalid = 6, with Copy results and Download CSV.
- Responsive: no horizontal overflow at 320 px (`scrollWidth == clientWidth`); the bulk
  table collapses to labelled cards below 760 px; verified at 320 / 375 / desktop.
- Tools menu opens with `aria-expanded` synced and exposes Files / Compare / Bulk.
- No console errors on `/`, `/bulk`, `/files`, `/compare`, `/reference`.

Independent review: a multi-agent pass re-read the implementation against the brief by
dimension (calculation, result states, bulk, accessibility, privacy, design system) and
adversarially verified each flag. Calculation, bulk and privacy came back clean. The
confirmed items were then fixed and re-checked in the browser: the identifier field now
holds 22px on mobile (was clamping to 20px); the mode tabs are 44px tall; identifiers use
a slashed zero; the file-load label shows a focus ring via `:focus-within`; the skip-link
target `<main>` is focusable; result tables carry `scope` headers and the bulk line column
reads "Line"; a focused element is kept clear of the sticky header; and the `/check`
scheme select gained a visible label. One item was left deliberately: the bulk CSV `status`
column keeps the kernel's own vocabulary (`corrected` for a proposed-but-unapplied check
digit) rather than the UI word "Mismatch" — the report is a faithful record of the kernel
decision, the entered value is preserved unchanged, and the Printed/Expected columns expose
the disagreement, so a mismatch is never presented in a *valid* or *confirmed* list.

Not independently verified (stated honestly):
- No usability testing with recruited participants was done; the expert findings the brief
  listed were addressed and re-checked against the current build, not validated with users.
- True browser page-zoom at 200% was not emulated in this harness; the layout reflows
  cleanly at 320 px (equivalent to ~200% of a 640 px viewport), which is the same code path.
- Automated contrast covers the design tokens; rendered-pixel contrast of every composed
  state was reviewed by eye, not by an automated screenshot contrast tool.
- The ILU (EN 13044) equivalence to ISO 6346 arithmetic remains labelled as not
  independently confirmed against the normative text, as before.
