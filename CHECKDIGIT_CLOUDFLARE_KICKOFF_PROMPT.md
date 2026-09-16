# CHECKDIGIT on Cloudflare — kickoff prompt for Claude Code

Paste everything inside the fence as the first message of a Claude Code session opened in
a directory that contains `CHECKDIGIT_SEED.md`, `CHECKDIGIT_COVERAGE_AND_ROADMAP.md`, and
the extracted `checkdigit/` tree from `checkdigit-full.tar.gz`. Target model: Claude
Opus-class or Fable in Claude Code. No variables to set; the decisions are baked in.

```
# CHECKDIGIT — build the public verification site on Cloudflare

## Decisions already made (do not reopen)
- Cost: $0. Cloudflare Workers Free plan with Static Assets, no paid add-ons, no paid
  APIs, no D1/KV/R2 bindings, no Durable Objects. If a step would incur a charge or
  require a paid plan, stop and say so.
- Storage: NONE. Nothing is stored server-side — no container numbers, no uploads, no
  audit rows, no counters, no client IPs, no analytics. There is no database. Everything
  the site does happens in the visitor's browser.
- Hosting: this replaces the self-hosted Docker/Caddy/Tunnel product; it is not deployed
  beside it. `checkdigit/` remains in the repo as the algorithm source of truth for
  parity tests only; its deployment files are never used.
- Scope: Phase 1 (verification site) and Phase 2 (in-browser file correction), in that
  order, each gated. There is no server-side API phase. If an edge endpoint is ever
  wanted later, it will be a separate design pass with its own prompt.

## Role and prime rules
You are the implementing engineer for CHECKDIGIT (thecheckdigit.com), a tool that verifies
and corrects shipping-equipment check digits. The existing Python tree is the source of
truth for every algorithm and behaviour. You are porting its verified behaviour to a
Cloudflare-hosted TypeScript site; you are NOT redesigning the algorithms.

Rules that override everything else:
1. Read before you write. Before touching any module, read the corresponding Python file
   in `checkdigit/` and quote the lines you are mirroring in a comment. Never edit from an
   assumed structure.
2. Never invent an algorithm, a segment position, a regex, a route, a library API, or a
   Cloudflare limit. If a fact is not in `checkdigit/`, `CHECKDIGIT_SEED.md`,
   `CHECKDIGIT_COVERAGE_AND_ROADMAP.md`, or the official Cloudflare docs you fetched this
   session, mark it `OPEN:` in `OPEN_ITEMS.md` and stop that thread.
3. Fail loud. A parity test that cannot run, a golden file that is missing, or a limit you
   cannot confirm is a hard stop with an explicit message — never a silent fallback or a
   skipped test.
4. Byte-splice, never re-serialize. Any code that changes file content must be an
   offset-verified in-place substitution; output length == input length; idempotent.
5. Route per standard, never cross-apply (ISO 6346 mod-11 vs UIC Luhn). Ambiguous ⇒ FLAG.
6. Context gates correction: CORRECT only in declared equipment fields or under explicit
   trust; free-text hits are FLAGGED.
7. No server-side state, ever, in this build. No page makes a network request after the
   static shell loads. If you find yourself writing a `fetch()` to anything but the
   site's own static assets, stop.
8. Zero third-party runtime dependencies in the browser bundle unless listed under
   "Allowed dependencies". The shipped site makes zero external network requests
   (no CDN scripts, no fonts, no analytics).
9. Work in numbered passes. End every pass with: what changed (file list), what was
   verified (command + result), what is OPEN. Wait for my confirmation before the next
   pass. Commit at the end of each green pass with a message naming the pass.

## Ground truth you must use (by exact name)
- `checkdigit/equipment_checkdigit.py` — the kernel. Letter table A=10…Z=38 skipping
  11/22/33; weights 2^0…2^9; remainder 10 → check digit 0 (`remainder_ten` flag);
  `normalize()` = strip `[\s\-]` then uppercase; regexes `_RE_BIC_LIKE`,
  `_RE_CONTAINER_SHAPED`, `_RE_UIC`; ISO categories `UJZ`, ILU categories `ABDEK`;
  `explain()` output shape; the CORRECT/FLAG decision table in `correct_identifier`.
- `checkdigit/test_equipment_checkdigit.py` (22 tests) and `checkdigit/run_pass11.py`
  (`write_js_vectors()` — kernel-truth vectors, deliberately messy: lowercase, spaces,
  UIC). Port both as the parity suite.
- `checkdigit/substitution.py` (+ `test_substitution.py`, 7 tests) — the only writer.
- `checkdigit/dispatcher.py` — `detect_format`, `detect_bytes` (ZIP/PDF/docx/pptx
  rejection), `correct_with_hint`.
- Locators/correctors: `edifact_*`, `x12_*`, `snx_*`, `txt_*`, `csv_*`, `fixedwidth_*`,
  `xlsx_*`.
- `checkdigit/nearmiss.py`, `checkdigit/policy.py`, `checkdigit/iso6346_sizetype.py`,
  `checkdigit/correction_report.py` (the `report` JSON shape).
- Golden byte-parity pairs: `checkdigit/samples/4Containers_snx_Example.xml` →
  `checkdigit/4Containers_snx_Example.CORRECTED.xml`;
  `samples/USER01_baplie_edi.txt` → `USER01_baplie_edi.CORRECTED.txt`;
  `samples/L02LOADLIST.txt` → `L02LOADLIST.CORRECTED_lenient.txt` (owner_policy=lenient);
  `X12_synthetic.CORRECTED.edi`. Plus `samples/txtcontainers.txt` (41 distinct: 3 valid,
  38 flagged under FREE_TEXT) and `samples/COPRAR_Discharge.xml` (30 units).
- Existing JS mirrors: the `/* CALC-PURE-BEGIN … END */` blocks in
  `checkdigit/checkdigit_app.jsx:69-119` (ISO/ILU + UIC), `site/index.html:362-372`,
  `site/check-digit.html:225-247`. Consolidate these into ONE module; do not leave three
  copies.
- Design tokens (from `CHECKDIGIT_SEED.md` §8): bg `#0E1418`, panel `#141C23`, raised
  `#1A242E`, line `#293743`, text `#E7EEF4`, muted `#90A0B0`, brand/corrected `#F3A93B`,
  valid `#52B98A`, flagged `#5B9DF0`, invalid `#E5604F`; monospace-forward, container IDs
  rendered fixed-width. Verdict colours are semantic and must not be reassigned.
- `CHECKDIGIT_COVERAGE_AND_ROADMAP.md` — §2 lists verified gaps and doc contradictions you
  must not reproduce; §4 is the feature backlog by tier.

## Platform (verified 2026-09-16 against developers.cloudflare.com)
- Cloudflare's own Pages overview says "Start new projects with Workers." Build a
  **Workers project with Static Assets only**: `wrangler.jsonc` with an `assets` binding
  and `not_found_handling: "single-page-application"` (or 404 page if you use real
  routes); **no Worker script at all** unless one is strictly required for routing — a
  pure static-assets deployment serves for free with no per-request CPU accounting.
  If you do need a script, it must contain no state and no outbound fetches.
- Worker handlers are JavaScript/TypeScript. The Python tree does NOT run there. Python
  Workers (Pyodide) exist but offer no filesystem or SQLite file; do not attempt to
  deploy `api.py`.
- Workers Free reference numbers: 10 ms CPU per request, 100 000 requests/day (these
  apply only if a script exists; static asset requests are free and unmetered). Re-fetch
  `https://developers.cloudflare.com/workers/platform/limits/` and
  `https://developers.cloudflare.com/workers/static-assets/` in Pass 0 and record what
  you saw in `OPEN_ITEMS.md`; if they differ from this, yours win.
- Custom domain: bind `thecheckdigit.com` (and `www`) to the Worker via Custom Domains
  in the dashboard (free on a zone already on Cloudflare). Write the exact steps in
  `DEPLOY.md`; do not touch DNS yourself.
- Consequence: all parsing and byte-splicing runs **in the browser** (a Web Worker).
  Nothing runs at the edge.

## Phase 1 — the verification site (definition of done below)
Deliver a static site with these routes/views. Everything runs client-side; there is no
network call on any of these pages.

1. `/` — hero with the 11-cell segmented ISO 6346 validator (owner 3 letters, category
   1, serial 6, check 1), prefilled `CSQU3054383`, live verdict as you type, anatomy
   strip, and the four-step explanation (Detect / Route / Correct / Review). Verdict states
   and colours: VALID (`#52B98A`), CORRECTED — meaning "the digit you typed is wrong; the
   correct digit is N" (`#F3A93B`), FLAGGED (`#5B9DF0`), INVALID structure (`#E5604F`).
2. `/check` — single-token calculator: accepts ISO 6346 / ILU (10 or 11 chars) and UIC
   (11 or 12 digits), shows the per-character table (char, value, weight, product), Σ,
   mod 11 (or Luhn steps), the remainder-10 note when applicable, the ILU category note,
   the "unknown category letter ⇒ flagged" note, and a shareable URL of the form
   `/check#<normalized-token>` that restores the state on load.
3. `/bulk` — paste box (also accepts drag-drop of a `.txt`/`.csv`) → extract tokens using
   the kernel's normalization and shape regexes → verdict table with columns
   `as_found, normalized, id_type, status, printed_check, computed_check, suggested`,
   summary counts by status, filters, and export as CSV (UTF-8 BOM + CRLF, same columns
   as the SPA's `exportCsv`) and JSON. Cap: 10 000 tokens; above that, refuse loudly
   with the count. Empty input renders an explicit "nothing to check" state, not a blank
   table. Tokens are extracted with the txt locator's semantics AND a documented
   extension: lowercase and hyphen/space-separated tokens are normalized first (this is
   an intentional difference from `txt_locator.py:36`; document it in the UI and in
   `PARITY.md`).
4. `/generate` — given a 10-character body, output the check digit; given a 4-character
   prefix (owner+category), output N random check-valid serials, marking any whose
   remainder is 10 as "avoid issuing (ISO 6346 guidance)". Use `crypto.getRandomValues`.
5. `/sizetype` — decode a 4-character size/type code using the tables in
   `iso6346_sizetype.py` (port the tables verbatim, including `defined=false` fallback
   and the deliberate omission of length code `5`; keep the two `VERIFY` notes visible).
6. `/how-it-works` — port of `site/check-digit.html`, with the letter table generated
   from the same table the code computes with, and the blind-spot callout (values
   differing by a multiple of 11, e.g. `1 ↔ B`) made interactive.
7. `/help` — port of `site/help.html`, corrected: the privacy section must say the
   truthful thing for THIS deployment ("your input is processed in your browser and is
   never sent anywhere; this site has no server and stores nothing"). Remove the
   self-hosting, Docker, and admin/sign-in sections entirely. Replace the landing-page
   card "Runs on your hardware … the file never leaves the box you run it on" with
   "Runs in your browser … the file never leaves this tab", and make that claim
   verifiable by the `site.test.ts` zero-network check.

Cross-cutting Phase 1 requirements:
- One shared module `src/lib/checkdigit.ts` exporting at least `normalize`,
  `iso6346Value`, `iso6346CheckDigit`, `uicCheckDigit`, `classify`, `explain`,
  `correctIdentifier` with the same enum values (`ISO6346|ILU|UIC|SIZE_TYPE|UNKNOWN`,
  `VALID|CORRECTED|FLAGGED|NOT_A_TARGET|INVALID_STRUCTURE`) and the same decision table
  as the kernel. Wrap the pure part in `/* CALC-PURE-BEGIN */ … /* CALC-PURE-END */`.
- Accessibility: every interactive cell labelled; keyboard-only path works for every
  view; contrast ≥ 4.5:1 for text on the tokens above; `prefers-color-scheme` honoured
  with the dark palette as default and a light palette derived from the same tokens.
- Mobile: usable at 360 px width with no horizontal scroll.
- Performance budget: first-load JS ≤ 60 kB gzipped for `/`, no render-blocking
  external resources, Lighthouse performance ≥ 95 on the deployed URL.
- Installable: web manifest + a minimal service worker that caches the static shell so
  every Phase 1 view works offline after first visit.

## Phase 2 — in-browser file correction (starts only after Phase 1 is confirmed green)
- `/correct` view: drop or pick a file (≤ 5 MB, same cap as the Python app; larger ⇒
  loud refusal with the size), or paste text. Controls: owner policy `strict|lenient`,
  `trust` toggle, format hint `auto|csv|fixed`, CSV column picker (auto-suggest columns
  whose values match `_RE_CONTAINER_SHAPED`), fixed-width range picker.
- Port, in this order and with a parity test for each before moving on: `substitution`
  → `dispatcher` (`detect_format`, `detect_bytes`) → `txt` → `edifact` → `x12` → `snx`
  → `csv` → `fixedwidth` → `xlsx` (ZIP member splice; a hand-written STORE/DEFLATE
  reader/writer using `DecompressionStream`/`CompressionStream` is acceptable; if you
  cannot preserve member bytes exactly, stop and mark OPEN rather than re-serializing).
- Run all parsing and splicing inside a Web Worker; the main thread only renders.
- Output: summary (total / corrected / flagged / valid / invalid / empty_id), corrections
  list with per-occurrence before/after diff and "kept in sync" for multi-occurrence IDs,
  flagged list with reasons and near-miss suggestions computed over the tokens in the
  same file (port `nearmiss.osa_distance` and `suggest`; the pool is the file's own
  check-valid bodies since there is no warehouse), inventory table with filters, CSV
  export, and "Download corrected" producing a file whose bytes differ from the input
  only at corrected check-digit characters.
- Batch: multiple files → a ZIP containing `corrected/<stem>.corrected<ext>`,
  `report.csv` and `manifest.json` with the column and key names from
  `checkdigit/batch.py`.
- Stowage visualization is OUT of scope for Phase 2 (see roadmap §2.1); do not port
  `bayplan_*` / `intermodal_*`.

## Explicitly not in this build
No API routes, no `/check/:token` endpoint, no admin, no dashboard, no history, no
container registry, no enrichment, no policy persistence (strict/lenient and any
allow/deny prefixes are per-session UI state only), no telemetry. If a later prompt asks
for any of these, it will come with its own storage and cost decisions; do not
pre-build hooks for them.

## Verification (the definition of done for each phase)
Create `tests/` with:
- `kernel.parity.test.ts` — every case from `test_equipment_checkdigit.py` and every
  vector from `run_pass11.write_js_vectors()`; regenerate vectors by running
  `python3 checkdigit/run_pass11.py` in a Python ≥ 3.12 environment and committing the
  JSON to `tests/vectors/`. Include at minimum: `CSQU305438→3`, `APLU100000→0`
  (remainder ten), `MSKU1234567` ⇒ mismatch with computed `5`, UIC `21812471217→3`,
  a lowercase/space-separated token, an unknown-category token (⇒ FLAGGED), a 12-digit
  token in non-rail context (⇒ FLAGGED even when Luhn passes).
- Phase 2: `splice.parity.test.ts` — for each golden pair above, the TypeScript output
  must be byte-identical to the `*.CORRECTED*` file and length-identical to the input;
  running the corrector on its own output must be a no-op (idempotence). `detect.test.ts`
  — `.docx`, `.pdf`, non-xlsx ZIP, corrupt ZIP rejected with the exact reasons in
  `dispatcher.detect_bytes`.
- `site.test.ts` — the built output contains zero `http(s)://` script/link/img/font
  references and zero `fetch(`/`XMLHttpRequest`/`WebSocket`/`sendBeacon` calls outside
  the service-worker's own asset caching; every CALC-PURE block in the build equals the
  shared module (no drift); every route above returns 200 from `wrangler dev`.
- `cost.test.ts` — `wrangler.jsonc` declares no bindings (`d1_databases`, `kv_namespaces`,
  `r2_buckets`, `durable_objects`, `queues`, `analytics_engine_datasets` all absent) and
  no `main` script unless `OPEN_ITEMS.md` records why one is required.
- A `PARITY.md` table: Python symbol → TypeScript symbol → test that proves it → status
  (PARITY / INTENTIONAL-DIFF (explain) / OPEN).
- Run `npm test`, `npm run build`, `npx wrangler dev` smoke, and (after my go-ahead)
  `npx wrangler deploy`; paste the results. Do not deploy without my confirmation.

## Allowed dependencies
Build/test-time: `wrangler`, `vite` or `esbuild`, `typescript`, `vitest`. Runtime in
browser: none by default. If you believe a runtime dependency is unavoidable (e.g. a ZIP
library for xlsx), stop and ask with the specific reason and size.

## Output contract for every pass
1. Files changed (paths).
2. Commands run and their exact results.
3. `PARITY.md` and `OPEN_ITEMS.md` updated.
4. One paragraph on what the next pass will do. Then stop and wait.

## Ambiguity rule
If two readings of a requirement change the code you would write, ask one question and
wait. If they do not, choose the reading closest to the Python behaviour, state the
assumption in the pass summary, and proceed.

## Do not
- Do not port `api.py`, `auth.py`, `db.py`, `service.py`, `enrichment.py`,
  `watch_folder.py`, `cf_access.py`, `batch.py`'s server paths, Docker/Caddy/Litestream/
  tunnel config, or the `bayplan_*`/`intermodal_*` renderers. They belong to the retired
  self-hosted product and stay in `checkdigit/` untouched as reference.
- Do not add any Cloudflare binding, paid feature, or third-party service, free tier or
  not (no analytics, no error reporting, no fonts CDN, no captcha).
- Do not reference `jayde.io` anywhere in this project.
- Do not reproduce the documentation errors listed in
  `CHECKDIGIT_COVERAGE_AND_ROADMAP.md` §2.2.
- Do not add analytics, fonts, or scripts from any external host.
- Do not weaken a verdict to make a test pass; fix the port or mark OPEN.

Begin with Pass 0: read the ground-truth files listed above, run
`python3 checkdigit/test_equipment_checkdigit.py` and `python3 checkdigit/run_pass11.py`
(report if your Python is < 3.12 and what that blocks), fetch the two Cloudflare docs
pages named under "Platform" and record the limits you saw, scaffold the Workers
Static Assets project with no bindings and no script, write `PARITY.md` with every row
set to OPEN, and stop for confirmation.
```
