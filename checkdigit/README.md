# CHECKDIGIT

Self-hosted check-digit validation, correction, and tracking for shipping and
terminal equipment identifiers. Drop in a COPRAR, BAPLIE, X12 manifest, an SNX
SNX export, Excel load list, CSV, fixed-width flat file, or plain text —
CHECKDIGIT finds every container number, recomputes each check digit with the
algorithm that identifier type actually uses, corrects it **in place without
touching a byte it shouldn't**, shows you exactly what changed, draws the
stowage where it applies, and remembers every box it has ever seen.

Built for a homelab. Stdlib-first: the entire correction core has **zero
third-party dependencies**; FastAPI/uvicorn serve it, httpx is lazy-loaded only
if you enable live enrichment. Concurrency-hardened for multiple simultaneous
sessions, and fronted by a guided web app with a built-in documentation tab.

## What it does

**Correction core** — ISO 6346 (BIC) and ILU (EN 13044) via the mod-11 scheme
(letter values skipping multiples of 11, 2^i weights, remainder-10 → 0); UIC
wagon numbers via Luhn mod-10, routed separately and never cross-applied; ISO
size/type codes (22G1…) recognized and *never* treated as correction targets.
Per-run `owner_policy` (strict flags pseudo-prefixes like L02U even when the
digit checks out; lenient corrects them) and a `trust` flag governing free-text
matches, with owner-registry corroboration in between.

**Formats** — EDIFACT (any message carrying EQD+CN; EQA gensets excluded),
ANSI X12 (N7 split prefix/serial + check element, N9*EQ; N9*BM bills of lading
untouched), SNX XML (four synced eqid attributes corrected together,
XXE-hardened), plain text (low-trust scan), **CSV/TSV** and **fixed-width**
flat files (opt-in column-scoped trust — you declare the container column or
character range; the declared field is corrected, every other column left
untouched, quoting/CRLF/padding byte-preserved), and **xlsx** — located inside
sharedStrings/inline-string text nodes, spliced per ZIP member, every untouched
member byte-identical; formula-cached values and rich-run-split tokens are
flagged, never edited. Format detection is content-based, never by extension
(CSV and fixed-width are never auto-trusted — opt-in only, since a CSV of
numbers is still valid plain text); docx/pptx/PDF/foreign ZIPs are rejected
with explicit reasons.

**Size/type decoding** — the four-character ISO 6346 size/type code (22G1,
45R1, L0G1…) is decoded to length / height / type-group / dimensions, from the
BIC/ISO tables, with an explicit *undefined* fallback (unknown codes are
flagged and shown raw, never guessed). Drives the visualizers; never a
correction target.

**The method** — parse to validate, regex to locate byte offsets, splice raw
bytes. Files are never re-serialized, which is why a corrected BAPLIE differs
from the original *only* at the corrected digits (asserted, not hoped). A
latin-1 EDI file comes back latin-1.

**Web app (React, single file)** — a guided interface where correction is the
headline act: an orienting hero (what it is, one primary "Validate a file"
action) over upload/paste (pasted EDIFACT is treated as EDIFACT); a live
check-digit calculator showing the full worked math (values/weights/Σ/mod 11/
remainder-10 rule), backed by a JS mirror cross-validated against the Python
kernel; before/after diff with check-slot chips; CSV verdict export. The nav
separates the primary **Correct** from the secondary tools — a **Dashboard**
(analytics over the warehouse), **History** (the audit log with per-run CSV
downloads), **Containers** (registry with per-box dossier: identity, seen-counts,
enrichment, every file it appeared in), and a **Docs** tab: a structured
technical reference with a sticky section nav covering the algorithms (with a
worked example), every format and its trust model, the correction guarantees,
the rules engine, batch/SFTP, enrichment, the data model, the visualizers,
privacy, deployment, and the full API. "Did you mean" near-miss chips throughout.

**API (FastAPI)** — `POST /correct` (file or `text` form field),
`POST /correct/batch` (many files or a zip → corrected zip + report),
`POST /visualize` (BAPLIE/rail/truck → isometric SVG),
`GET /check/{token}` (calculator + near-misses, read-only by design),
`GET /insights` (dashboard analytics: totals, trend, league tables, lanes),
`GET /events` + `GET /events/{id}/containers.csv`, `GET /containers` +
`GET /containers/{eqid}`, `GET /policy` + `PUT /policy` (operator rule layer),
`GET /sources`, `GET /portwatch/{iso3}` (IMF PortWatch port-activity by country —
open data, port-level context, not container enrichment), `GET /health`.
OpenAPI docs free at `/docs`.

**Tracking & audit (SQLite + Litestream)** — every distinct container with
owner/category/type and first/last/times seen; every run with filename, type,
detected format, size, user-agent, timestamp, and verdict counts. **No client
IP is ever stored or read** — a deliberate privacy deviation from the original
spec, locked early.

**Near-miss suggester** — when a check fails, the digit *or the body* is wrong.
The corrector handles the digit branch; the suggester serves the body branch by
proposing previously-seen, check-valid containers within OSA edit distance ≤ 2
of the failing body (distance 0 = "this exact body is known; the digit was the
error"). Proposals only — never auto-applied.

**Enrichment** — offline BIC owner-register lookup inline (also powers
free-text corroboration); live sources strictly opt-in via env credentials,
queried in a background task off the correction path: BIC BoxTech (verified
from its OpenAPI spec), Maersk Track & Trace, and a generic DCSA events adapter
you point at your Hapag-Lloyd/CMA CGM/ZIM portal gateway. Each adapter carries
an AUTH block naming its registration URL and exact env vars. Thirteen sources
classified FREE-OPEN / FREE-WITH-AUTH / PAID / GATED at `GET /sources`; nothing
unverified is ever called.

**Headless ingestion** — `watch_folder.py` (stdlib) watches an inbox, gates on
size-stability + age so half-written SFTP uploads are never read, runs the
identical pipeline, writes corrected file + verdict CSV to an outbox, archives
originals, quarantines failures with reasons, and never dies on one bad file.
SFTP itself is delegated to an OpenSSH container (`atmoz/sftp`) in compose — no
SFTP code was written, on purpose.

**Operator rules engine** — beyond the per-run strict/lenient choice, a
persistent policy layer resolves per 3-letter owner prefix in the order
**deny → allow → per-prefix → default**: `deny` always flags for review (even a
valid digit), `allow` treats a prefix as corroborated so failures correct at
low trust, `per-prefix` overrides strict/lenient for named owners, wildcards
supported (`TES*`). JSON, settable live via `PUT /policy` or a mounted file,
applied atomically at the single correction choke point so it threads every
format identically.

**Batch / bulk** — `POST /correct/batch` takes many files, or a single `.zip`,
and returns a zip of each corrected file plus a consolidated `report.csv` and
`manifest.json`. Failure-isolated: a rejected or malformed member is recorded
and skipped, never sinking the batch, and every member is audited like a normal
upload.

**Stowage visualizers** — when a file describes a physical move, CHECKDIGIT
renders it as a dependency-free isometric SVG from the *corrected* data:
*vessel bay plan* (BAPLIE / ISO 9711 bay-row-tier, looking forward, on-deck vs
in-hold split, reefer/hazmat/oversize/HC/empty callouts); *rail* (doublestack
well-car cross-section + train-consist sequence, X12 404/418, EDIFACT); *truck*
(container-on-chassis, front/rear on a tandem, COPINO/CODECO). The honesty rule
is enforced visually: rail well/tier and truck front/rear are **not** carried
in standard EDI — they're inferred from equipment type + count/size + loading
rules, so inferred geometry is drawn dashed with an "inferred" marker and a
banner says so. Available at `POST /visualize` and inline on `/correct`.

**Analytics dashboard** — `GET /insights` aggregates the warehouse into
headline KPIs, a per-day error/volume trend, a fix-rate league table by owner
("who ships the most broken numbers"), fix rate by file format, busiest
locations and vessels, and origin→destination lanes derived from consecutive
container events (SQL window function). The SPA renders it with inline-SVG
charts — no chart library. Every panel degrades to an empty state before its
warehouse table has rows.

**Concurrency** — built to take simultaneous load (many uploads plus the SFTP
worker, all sharing one SQLite DB). WAL journaling; `busy_timeout` so a writer
that meets a held lock *waits* instead of failing with "database is locked"
(the default-0 trap); `synchronous=NORMAL`; per-request connections; a
write-retry helper for the rare checkpoint contention; atomic policy swaps under
a lock; and race-safe schema init across concurrent worker startups.
Load-tested under parallel uploads and multiple processes.

## Run it

**Fastest path:** `./install.sh` (Python 3.10+; creates a venv, installs deps,
builds the web UI if Node is present, and starts the server on
`http://127.0.0.1:8000`). Full options — Docker, manual, and public-exposure —
are in **`INSTALL.md`**. The manual equivalent:
curl -F "file=@loadlist.xlsx" "http://localhost:8000/correct?owner_policy=strict"
curl -F "text=MSKU7351773 recheck" "http://localhost:8000/correct"
curl "http://localhost:8000/check/CSQU3054383"
curl -F "file=@batch.zip" "http://localhost:8000/correct/batch" -o corrected.zip
curl -F "file=@vessel.baplie" "http://localhost:8000/visualize" -o stowage.svg
curl "http://localhost:8000/insights"
curl -X PUT "http://localhost:8000/policy" -H 'content-type: application/json' \
     -d '{"default_policy":"strict","deny":["TES*"],"per_prefix":{"MSC":"lenient"}}'
```

`docker-compose.yml` runs app + watch-folder worker + SFTP + Litestream backup
(SQLite in WAL mode is what makes the API/worker share safe; see DEPLOY.md for
the multi-worker concurrency notes). For exposure: Tailscale first;
`docker-compose.public.yml` + `Caddyfile` + Cloudflare Tunnel/Access for a
public URL — `DEPLOY_PUBLIC.md` spells out the TLS and AT&T-residential
tradeoffs. Replace `owner_registry.seed.csv` (observational sample) with the
real BIC register download.

**Private workspace (large files, durable jobs).** `workspace/` adds admin-gated
routes under `/v1/workspaces/{workspace}` for resumable uploads, declared import
intents, streamed validation jobs, cursor-paginated review, storage-backed
approvals, streamed exports and atomic generation publication. State lives under
`CHECKDIGIT_WORKSPACE_DIR` (default `workspace-data`). Run a worker with
`python3 -m workspace.runner workspace-data/workspace.db workspace-data`, or call
`POST .../jobs/{id}/run` to execute queued work inside the request. The recorded
three-million-record run is in `../BENCHMARK.md`; reproduce it with
`python3 -m workspace.bench generate|run|verify`.

The SPA (`checkdigit_app.jsx`) is a single-file React app with Correct,
Dashboard, History, Containers, and Docs views: drop it into a Vite React
scaffold and build into `static/` for same-origin serving; it also runs
standalone on embedded sample fixtures with no backend.

## Verification

`run_acceptance.py` maps every original requirement and agreed feature to a
live assertion. Behind it: kernel unit tests (22), substitution tests (7),
per-format drivers (passes 4–7), service/audit (8), enrichment (9–10),
calculator + CSV (11), history/dossier (12), near-miss incl. a 400-pair fuzz
against a naive reference DP (13), xlsx incl. verbatim-member proofs (14), the
watch-folder incl. latin-1 byte-fidelity (15), CSV/fixed-width column-trust +
byte fidelity (17), the operator rules engine across formats (20), batch incl.
zip I/O and failure isolation (21), the dashboard warehouse queries incl. lane
reconstruction and empty-state safety (22), the BAPLIE vessel viewer (23), the
rail/truck intermodal viewers incl. inference flagging (24), and concurrency
incl. busy_timeout-waits, cross-process writers, parallel uploads with no lost
updates, schema-init race, and policy-swap safety (25). The JS calculator is
executed under node against kernel-generated truth vectors, and the SPA JSX is
compiled with esbuild. Everything passes in a network-less sandbox — by design,
since the core is stdlib. Run the full sweep:

```bash
python3 test_equipment_checkdigit.py; python3 test_substitution.py
python3 test_contracts.py; python3 test_phase_a.py; python3 test_workspace.py
for p in 8 9 10 11 12 13 14 15 16 17 20 21 22 23 24 25; do python3 run_pass$p.py; done
python3 run_acceptance.py
```

## Honest limits

FastAPI/uvicorn and Docker were statically validated but not *executed* in the
build environment (no pip/network there) — first `uvicorn` run is yours, as is
opening a corrected workbook in real Excel. Hapag/CMA/ZIM gateway bases require
your portal account (the generic adapter takes them; nothing was guessed).
EDIFACT envelope repair is out of scope.

**VGM / reefer-setpoint / dangerous-goods field validation (the BAPLIE deep
checks) is deliberately not built** — it requires real BAPLIE/VERMAS sample
files to ground the exact segment positions, and fabricating those positions
would violate this project's grounding rule. It's the one genuinely outstanding
feature, parked until real samples are supplied.

**The rail/truck viewers' well/tier and front/rear positions are inferred**, not
read from the EDI (which doesn't carry them) — drawn distinctly and labelled as
such. If you supply real carrier samples that populate the mutually-defined
position element, those views can be switched from inferred to verified
per-carrier.

Near-miss suggestions are computed per run and not persisted. Rebuilt xlsx
preserves member *bytes*, not ZIP member timestamps. Concurrency is hardened for
many simultaneous sessions on **one host** — SQLite is single-host by design;
horizontal scale-out across machines would mean swapping in Postgres, out of
scope for the homelab. No in-app auth — front it with Tailscale or Cloudflare
Access.
