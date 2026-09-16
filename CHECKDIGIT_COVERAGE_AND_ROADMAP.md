# CHECKDIGIT — Coverage Matrix & Web Roadmap

> Status note (Phase A, 2026-09-16): the current capability map is `CAPABILITIES.md`;
> this file is kept as the review-time coverage matrix and backlog. Section 2.2's
> documentation contradictions concern the retired self-hosted product's pages, which
> the static site does not reproduce. The gaps in section 2.1 that Phase A closed are the
> hinted CSV/fixed-width trust behaviour (review-only now stays review-only) and the
> `/correct` response dropping `encoding` and `visualization`.

Companion to `CHECKDIGIT_SEED.md`. Everything in the *Handled* / *Not handled* columns was
verified against the source tree in `checkdigit-full.tar.gz` (105 files) on 2026-09-16;
`file:line` references point into that tree. The *Brainstorm* section is recommendation,
not verified fact, and is labeled as such.

Baseline note: `python3 run_acceptance.py` is green through A11 on Python 3.11 and fails at
A12 only because `bayplan_render.py:227` uses a backslash inside an f-string expression,
which is legal on 3.12+ (the Dockerfile pins `python:3.12-slim`). `INSTALL.md:164` says
"Python 3.10+" — that claim is wrong; the true floor is 3.12. Fix one or the other.

---

## 1. Use cases HANDLED (verified)

### 1.1 Check-digit mathematics (kernel `equipment_checkdigit.py`)

| Use case | Status | Evidence |
|---|---|---|
| ISO 6346 mod-11 (A=10…Z=38 skipping 11/22/33; weights 2^0…2^9; remainder 10 → 0) | Handled | `:51-92`; live: `CSQU305438→3`, `APLU100000→0` |
| ILU / EN 13044 (categories A/B/D/E/K) using the same arithmetic | Handled, **unconfirmed vs. normative text** | `:32-33` FLAG comment |
| UIC 12-digit wagon numbers, Luhn mod-10 | Handled in kernel, calculator, `/check` only | `:106-114`; live `21812471217→3` |
| Explain / worked math per character (value × weight = product, Σ, mod, remainder-ten) | Handled | `explain()` `:522-581` |
| Normalization: strip whitespace and hyphens, uppercase | Handled | `:194` |
| Classification by shape + category letter; unknown category ⇒ FLAGGED not guessed | Handled | `:176-180`, `:282-291` |
| Context-gated correction (EQUIPMENT_ID / UNKNOWN ⇒ correct; FREE_TEXT ⇒ flag unless owner corroborated; SIZE_TYPE ⇒ never a target) | Handled | decision table `:267-398` |
| Owner policy strict/lenient for pseudo-prefixes like `L02U` | Handled | `policy.py`, acceptance `SCOPE` |
| Policy rule layer deny → allow → per-prefix (exact, then longest `*` wildcard) | Handled (API `PUT /policy`, no SPA editor) | `policy.py` |
| Near-miss suggestions (OSA distance ≤2 over seen check-valid bodies) | Handled, computed per run, not persisted | `nearmiss.py`; wired `service.py:117-126`, `/check` |
| Size/type code decode (`22G1`, `45R1`…) for context only | Handled (visualizers only) | `iso6346_sizetype.py`; code `5` deliberately unmapped |

### 1.2 File formats and in-place byte splice

| Format | Declared field(s) treated as equipment IDs | Notes |
|---|---|---|
| EDIFACT (BAPLIE, COPRAR, COPARN, COARRI…) | `EQD` C237/8260 only when `EQD01 == CN`; honours `UNA` separators + release char | `EQA` deliberately excluded (`edifact_locator.py:15-18`) |
| X12 (322/404/418…) | `N7-01`+`N7-02`, check in `N7-18`; `N9*EQ` full token | Present-but-wrong `N7-18` spliced; **absent `N7-18` FLAGGED, never inserted** (`x12_corrector.py:61-70`) |
| SNX / Navis container XML | `container/@eqid`, `equipment/@eqid`, `unit/@id`, `unit/@unique-key`, `line-discharge-list/@unit-id` | DOCTYPE/ENTITY refused (XXE); parsed-count vs byte-scan IntegrityError; synced occurrences replaced together |
| Plain text | regex `[A-Z]{4}[0-9]{7}` (uppercase only, no word boundaries) | FREE_TEXT ⇒ flagged unless `trust=true` |
| CSV / TSV | opt-in `columns` (0-based index or header name); delimiter sniffed `, \t ; \|`; RFC 4180 | **Not auto-detected** — needs `format_hint` |
| Fixed-width | opt-in 1-based inclusive `ranges` + `header_lines` | Not auto-detected |
| XLSX | `sharedStrings.xml` `<t>` + inline strings; ZIP member-byte splice | Formula-cached values, split rich-text runs ⇒ always flagged; ZIP timestamps not preserved |
| Rejected loudly before decode | `.docx`, `.pptx`, `.pdf`, non-xlsx ZIP, corrupt ZIP | `dispatcher.detect_bytes` `:134-161` |

Byte-splice guarantee: `substitution.apply_edits` is the only writer; every edit is
offset-verified; output length equals input length; idempotent (acceptance `R1-R5/SNX`).
Golden pairs exist in the tree for regression: `samples/4Containers_snx_Example.xml` →
`4Containers_snx_Example.CORRECTED.xml`, `samples/USER01_baplie_edi.txt` →
`USER01_baplie_edi.CORRECTED.txt`, `samples/L02LOADLIST.txt` →
`L02LOADLIST.CORRECTED_lenient.txt`, plus `X12_synthetic.CORRECTED.edi`.

### 1.3 Service, API, persistence

| Use case | Status |
|---|---|
| `POST /correct` (file or pasted text; `owner_policy`, `trust`, `format_hint`, `columns`, `ranges`) → `{status, detected_format, filename, event_id, report}` | Handled |
| `POST /correct/batch` (multipart list or ZIP) → ZIP with `corrected/`, `report.csv`, `manifest.json`, `X-Batch-Totals` header | Handled (no SPA UI) |
| `GET /check/{token}` stateless explain + near-misses | Handled |
| `POST /visualize` — BAPLIE isometric bay SVGs; rail consist / double-stack / chassis SVG | Handled (no SPA UI; positions for rail/truck are inferred and drawn dashed) |
| Audit warehouse (SQLite): `ingestion_events`, `event_containers`, `containers` counters, owners, enrichment tables; **no client IP column** | Handled |
| Admin: `/insights`, `/events`, `/events/{id}/containers.csv`, `/containers`, `/containers/{eqid}`, `/policy`, `/sources`, `/portwatch/{iso3}` behind Google OIDC allow-list re-checked per request | Handled, live-verified 2026-08-28 |
| 5 MB body cap (Caddy + app), rejections audited | Handled |
| Enrichment adapters (BoxTech, carriers, DCSA, Terminal49…) tier-classified, all `wired=False` | Handled as scaffolding; dark by default |
| Watch-folder / SFTP inbox worker | Handled (compose `worker` + `sftp`) |
| Public static site (`site/index.html`, `help.html`, `check-digit.html`) self-contained, zero external requests, CALC-PURE blocks proven equal to kernel (A14) | Handled |

### 1.4 SPA (`checkdigit_app.jsx`, 2 002 lines)

Correct view (drop/pick or paste; strict/lenient; trust; service URL; three embedded
sample fixtures), live calculator with per-character table and UIC, near-miss chips via
`/check`, results summary, client-side CSV export, "Download corrected" (text or base64
xlsx), per-occurrence diff lines, inventory table with filters, stowage view for the
bundled BAPLIE fixture, Dashboard / History / Containers dossier (admin), Docs view.
Four-state auth machine (`null / "offline" / false / {email}`).

---

## 2. Use cases NOT handled (verified gaps and contradictions)

### 2.1 Functional gaps

| Gap | Evidence / consequence |
|---|---|
| **Stowage visualization never appears after a live upload.** `service.process_upload` builds `visualization` but `POST /correct` drops it; SPA reads `data.visualization`. | `service.py:143-171` vs `api.py:288-294`, `jsx:863`. Only the bundled BAPLIE fixture ever shows stowage. |
| **UIC numbers are never corrected in files.** No locator assigns `FieldContext.RAIL_VEHICLE`, so any 12-digit token in a file is FLAGGED even when Luhn passes. | `equipment_checkdigit.py:388-398`; grep of locators. Rail EDI (X12 404/418) is "supported" for visualization but not for wagon-number correction. |
| **Absent X12 `N7-18` is flagged, never inserted.** Because insertion would change file length (violates byte-splice). | `x12_corrector.py:61-70` |
| **Lowercase / pseudo-prefix tokens in plain text are invisible.** Regex is `[A-Z]{4}[0-9]{7}` — so `lenient` policy has no effect on `.txt`, and `msku1234567` is not found. | `txt_locator.py:36` |
| **CSV and fixed-width are not auto-detected**; the SPA exposes no `format_hint`, `columns`, or `ranges` controls. So the two most common "spreadsheet export" cases are API-only. | `dispatcher.py:81-127`; SPA has no UI |
| **No batch UI, no `/visualize` UI, no policy editor** in the SPA. | jsx views list |
| **No single-number verify-as-you-type "bulk paste" mode** on the public site (paste 200 numbers, get a verdict table). The SPA's paste box goes through `/correct` as a text file — works but logs an audit row and needs the backend. | design |
| **Near-misses are not persisted**; recomputed per run. | README "Honest limits" |
| **BAPLIE deep checks (VGM, reefer setpoint, DG) not built.** | README `:200-225` |
| **XLSX: formula cells, split rich-text runs, ZIP timestamps** — flagged / not preserved. | `xlsx_locator.py` |
| **SNX with DOCTYPE/ENTITY refused** (correct security stance, but a real N4 export with a DTD would be rejected rather than corrected). | `snx_locator.py` |
| **ILU / EN 13044 arithmetic unconfirmed** against the paywalled normative text. | kernel `:32-33`, seed §3.2 |
| **Owner registry is observational** (`owner_registry.seed.csv`), not the BIC register. | seed §14 |
| **Single-host SQLite**; no multi-user, no tenancy. | seed §14 |

### 2.2 Documentation contradictions (fix before anything goes public)

| Claim | Where | Reality |
|---|---|---|
| "No in-app auth — front it with Tailscale or Cloudflare Access" | `README.md:224`, `RUN_ON_LAPTOP.md:110` | `auth.py` + `api.py:105-115` mount Google OIDC; `DEPLOY_PUBLIC.md §0.5` forbids blanket Access |
| `/visualize` curl saves `-o stowage.svg` | `README.md:157` | route returns JSON |
| "the file never leaves the box you run it on" | `site/index.html:323` | `help.html:221` and `DEPLOY_PUBLIC.md §0` admit Cloudflare sees plaintext on the tunnel model |
| 415 message lists "EDIFACT, X12, SNX XML, or plain text" | `jsx:829` | xlsx is also accepted |
| "Python 3.10+" | `INSTALL.md:164` | 3.12 required (`bayplan_render.py:227`) |
| `dashboard_preview.html` loads React/Babel from cdnjs | `:3-5` | violates the "zero external requests" rule the `site/` pages follow — fine as a mockup, must not ship |

---

## 3. Cloudflare feasibility (checked 2026-09-16)

| Fact | Source |
|---|---|
| Cloudflare's Pages overview now says: "Workers supports most Pages use cases and offers a broader feature set. It is Cloudflare's primary platform for building applications. **Start new projects with Workers.**" Pages is not deprecated. | developers.cloudflare.com/pages |
| Pages Functions / Workers routes are JavaScript/TypeScript (V8). Python Workers exist (Pyodide; FastAPI/httpx/pydantic run; ~1 s cold start) but they are a separate Worker type, not a drop-in for the FastAPI+SQLite+filesystem app; **no persistent filesystem, no Litestream, no SQLite file.** | blog.cloudflare.com/python-workers-advancements |
| Workers Free: **10 ms CPU per request**, 100 k req/day; Paid: 30 s default up to 5 min. Request body: 100 MB on Free/Pro zones. Script size 64 MiB. | developers.cloudflare.com/workers/platform/limits |
| D1 (SQLite-compatible): 500 MB/db and 5 GB/account on Free; single-threaded per DB; 30 s query timeout. | developers.cloudflare.com/d1/platform/limits |

Consequences for the design: the Python core is **not** portable as-is. The correction
engine must be ported to TypeScript to run either in the browser or in a Worker. Byte-
splicing a 5 MB BAPLIE inside a 10 ms CPU budget is not credible on the free tier, so file
correction belongs **in the browser (Web Worker)**, which also honestly delivers the
"file never leaves your machine" promise the landing page currently makes and the tunnel
deployment breaks. The Worker/edge tier should carry only the cheap stateless calls
(`/check/:token`, single-number and small-batch JSON) and, later, D1-backed audit/admin.

---

## 4. Brainstorm — what should be added (recommendations, not verified facts)

Ordered by value-to-effort for a public verification site.

### Tier 1 — the verification site (client-side only, no backend)

1. **Bulk paste verifier.** Textarea → up to N thousand tokens → verdict table
   (VALID / CORRECTED / FLAGGED / INVALID), counts, copy/CSV/JSON export, all in-browser.
   This is the single most-requested thing a "check my container numbers" site does and
   the current tree has no dedicated mode for it.
2. **Single-number calculator with per-character explain** (already exists in three
   places — consolidate into one shared TypeScript module with the CALC-PURE contract and
   the kernel golden vectors).
3. **Generate the check digit for a 10-character body** (owner+category+serial) and
   **generate valid serials for a prefix** — useful for trainers building fixtures and
   testers seeding N4. Mark remainder-ten serials as "avoid issuing" per the standard.
4. **Shareable permalinks** `/#MSKU1234565` and `/check/MSKU1234565` so a verdict can be
   pasted into a ticket or Teams message.
5. **Owner-prefix lookup** (BIC code → company) from a bundled, versioned snapshot of
   `owner_registry.seed.csv`, with a visible "snapshot date" and a "verify at bic-code.org"
   link. Never call BIC live from the browser.
6. **Size/type decoder** (`22G1` → 20 ft, 8 ft 6 in, general purpose) — the module exists
   (`iso6346_sizetype.py`), it just has no public UI.
7. **Blind-spot demonstrator** — show the `1 ↔ B` class of undetectable substitutions
   interactively (values differing by multiples of 11). Trainers love this; it is honest.
8. **Keyboard-first UX**: paste → Enter → verdict; Tab through segments; screen-reader
   labels on each of the 11 cells; `prefers-color-scheme` support (tokens already exist).
9. **Offline / installable (PWA manifest + service worker)**: the site is already
   self-contained; making it installable lets gate clerks use it with no signal.
10. **OCR-friendly input**: accept `MSKU 123456 5`, `MSKU-123456-5`, lowercase, `O/0`
    and `I/1` confusables with an explicit "we normalized this" note (never silent).

### Tier 2 — in-browser file correction (the current core, ported to TypeScript)

11. Port `substitution`, `dispatcher.detect_format` / `detect_bytes`, and the seven
    locator/corrector pairs to TypeScript; run in a Web Worker; prove **byte-for-byte
    parity** against the four `*.CORRECTED*` golden files and the Python kernel vectors.
12. Before/after diff and "Download corrected" — same UX as the SPA, but the file never
    leaves the tab. Fixes the landing-page privacy contradiction for real.
13. Expose the API-only knobs the SPA hides: CSV column picker (auto-suggest columns whose
    values match the container regex), fixed-width range picker with a ruler, batch drop
    of many files → ZIP (JSZip-class library, or a hand-rolled STORE-only writer to keep
    the zero-dependency spirit).
14. **Wire stowage into the live result** (fix the dropped `visualization` block) or
    explicitly drop the feature from the web build — but not the current half-state.

### Tier 3 — edge API + optional persistence (Workers + D1)

15. `GET /api/check/:token` and `POST /api/verify` (JSON array ≤ 1 000 tokens) as Worker
    routes — cheap, stateless, cacheable, fits the 10 ms budget.
16. Opt-in anonymous telemetry ("count verdicts, never store numbers") — decide explicitly;
    the current design stores canonical container numbers, which on a public free site may
    be more than you want to hold. If audit is wanted, D1 with the existing schema minus
    anything the Tier-1 site does not need.
17. Admin surface via **Cloudflare Access on `/admin/*` only** (viable now that the OAuth
    callback no longer exists in the Worker model) or port `auth.py` to a Worker with
    Google OIDC — pick one, not both.
18. Rate limiting binding on `/api/*` (Workers-only feature; another reason to prefer
    Workers over Pages).

### Tier 4 — domain extensions (require verification work first)

19. **Rail vehicle correction in files** — add a locator that assigns `RAIL_VEHICLE`
    context for X12 404/418 car-number segments and EDIFACT rail messages; currently the
    kernel supports it and nothing feeds it.
20. **EN 13044 confirmation** — obtain the normative text (paid) or a published
    equivalence, close the FLAG.
21. **Real BIC register** replacing the observational seed; add ILU register (UIRR) if
    ILU support is to be more than arithmetic.
22. **BAPLIE deep checks** (VGM presence, reefer setpoint range, DG segments) — needs real
    samples; keep out of the correction path (report-only).
23. **X12 `N7-18` insertion** as an explicit, opt-in, non-byte-preserving mode with a
    loud banner ("this rewrites segment lengths") — or keep it flagged; decide.
24. **Training mode** — worked examples, quiz ("what is the check digit?"), and a printable
    one-page reference; aligns with the trainer audience and the `check-digit.html` page.

### Explicitly parked / out of scope for the web build

Enrichment adapters (all dark), SFTP watch folder, Litestream, Docker/Caddy/tunnel,
`portwatch`, vessel/location tables — none of these belong on a static edge site.
