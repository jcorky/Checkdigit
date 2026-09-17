# Capabilities

What Checkdigit actually does today, surface by surface. "Connected" means reachable
from a user interface in the root build; "tested" names the test that proves it;
"deployed" refers to the static site deployment described in `DEPLOY.md`. An adapter
class, a route in a Python module, or a design document is not a customer capability
and is not listed as one.

Status vocabulary: **implemented** (code exists and runs), **connected** (reachable
from the current UI), **tested** (automated test in this repository), **provider-tested**
(exercised against a real external system), **production-enabled** (switched on for a
deployment). Anything not marked is not claimed.

## Public site (static, `src/`)

| Capability | Status | Evidence |
|---|---|---|
| Segmented eleven-cell validator with whole-number entry, paste redistribution, overflow refusal, clear, visible normalization, explicit refusal of non-identifier characters with a proposal | implemented, connected, tested | `tests/segmented.test.ts` |
| Single-number check with per-character arithmetic, Luhn steps, remainder-10 note, category notes, one result per validation layer, scheme selection, share link, copy result | implemented, connected, tested | `tests/kernel.parity.test.ts`, `tests/validation.test.ts`, `tests/site.test.ts` |
| Check a list (`/bulk`): paste or drop a text/CSV file, normalized extraction with the original kept, per-layer results, filters, CSV (BOM, CRLF) and JSON export, 10,000-token cap refused with the count, explicit empty state | implemented, connected, tested | `tests/bulk.test.ts` |
| Files (`/files`): local inspector in a Web Worker for plain text, delimited (with a reviewed column mapping), fixed-width, EDIFACT, X12 and container XML; import intent declared before analysis; findings; proposals with evidence, occurrences and same-file near-misses; per-row and filtered bulk decisions with exact counts; change-set fingerprint; stale approvals on re-analysis; surgical export with exceptions CSV, change ledger CSV and manifest JSON; output reparsed before it is called ready | implemented, connected, tested (model and ports); browser workflow verified manually | `tests/localjob.test.ts`, `tests/mapping.test.ts`, `tests/splice.parity.test.ts`, `tests/detect.test.ts` |
| Mapping contract checks for delimited feeds: named reorder continues, positional column-count change blocks, duplicate or missing required headers block, extension columns preserved, full-stream identifier validation with late-violation blocking and isolated-record reporting | implemented, connected, tested | `tests/mapping.test.ts` |
| Compare (`/compare`): two lists or files, whole-identifier or body key, only-in-A/only-in-B/both/conflict/repeated outcomes, raw values kept beside comparable values, CSV export | implemented, connected, tested | `tests/compare.test.ts` |
| Reference library (`/reference`): anatomy, arithmetic with the generated letter table, interactive blind-spot demonstrator, size/type decoder, ILU and UIC notes, prefix registration and BoxTech, reference code sets, mass/VGM/reefer/DG terminology, terminal error patterns, file-mode limits, glossary, keyboard guidance; each article names its source and review date; search; quick checker | implemented, connected | `tests/sizetype.test.ts` for the decoder; article content reviewed 2026-09-16 |
| Independence from private history; zero network calls; no external resource loads; Electric Yard tokens on light and dark; contrast checks | implemented, tested | `tests/site.test.ts`, `tests/cost.test.ts` |
| Excel workbooks in the local inspector, offline manifest and service worker, saved mapping recipes, several-file batches in the browser | not built | later phases |

## Browser ports of the Python parsers (`src/lib/formats/`)

| Port | Proven by | Notes |
|---|---|---|
| Substitution (offset-verified, overlap-checked) | golden pairs, format vectors | offsets are UTF-16 indices (`offset_kind: utf16_unit`) |
| Plain text, EDIFACT, X12, container XML (SNX), delimited, fixed-width | `tests/splice.parity.test.ts`: byte-identical output for the four golden pairs, idempotence, 36 Python-generated fixtures (positive, negative, malformed, no-edit) compared field for field | container XML uses a start-tag scanner with balance and depth checks instead of a full parser |
| Detection and binary front door | `tests/detect.test.ts` | ZIP central directory read without decompression |
| Operator policy, near-miss ranking, size/type decoder | `tests/kernel.parity.test.ts` (743 cases through the ported policy), `tests/sizetype.test.ts` (46 codes) | |

## Python service (`checkdigit/`), not exposed by the root build

| Capability | Status | Evidence |
|---|---|---|
| Identifier kernel (ISO 6346, ILU, UIC), decision table, ASCII-only characters | implemented, tested | `test_equipment_checkdigit.py` (22), `test_phase_a.py`, 743 decision vectors shared with TypeScript |
| Offset-verified surgical substitution; code-point offset semantics with byte and UTF-16 converters | implemented, tested | `test_substitution.py` (7), `test_phase_a.py` |
| Content-based detection; binary front door | implemented, tested | `run_acceptance.py` A6 |
| EDIFACT, X12, SNX, plain text, CSV, fixed-width, XLSX correction | implemented, tested (fixtures) | `run_pass*` drivers, golden files |
| Review-only processing changes nothing | implemented, tested | `test_phase_a.py` |
| Raw value, normalized form, mathematical candidate and identity basis kept distinct | implemented, tested | `test_phase_a.py` |
| Ingress body-size middleware, per-route bounded reads, archive and workbook expansion budgets, time budget | implemented, tested | `test_phase_a.py`, `limits.py` |
| Bounded indexed near-miss retrieval with provenance | implemented, tested | `test_phase_a.py` |
| Public `/check/{token}` arithmetic-only; workspace candidates behind `/workspace/nearmiss/{token}` | implemented, tested | `test_phase_a.py` |
| `/correct` returns encoding, offset kind, visualization and an API version | implemented, tested | `test_phase_a.py` |
| Enrichment storage policies | implemented, tested | `test_phase_a.py` |
| Enrichment adapters (BoxTech, Maersk, CMA CGM, DCSA-style carriers, Terminal49) | implemented, **not provider-tested**, not production-enabled | no credentials in this repository |
| SQLite history, owner and event warehouse, insights; admin routes behind Google OIDC | implemented, tested (drivers) | `run_pass12`, `run_pass16`, `run_pass22`, `run_pass26` |
| ZIP batch with member budgets and distinct member identities | implemented, tested | `test_phase_a.py`, `run_pass21.py` |
| Watch folder / SFTP inbox | implemented, tested (smoke) | acceptance A7; not durable |
| BAPLIE and intermodal scene models and SVG rendering | implemented, not connected to the root build | `run_pass23`, `run_pass24` |
| Shared contract data | implemented, tested both sides | `test_contracts.py`, `tests/contracts.test.ts` |
| Private workspace (`checkdigit/workspace/`): immutable source store, import intents with idempotent fingerprints and contract coverage scopes, durable job queue with leases, heartbeats, checkpoints, retries, cancellation and crash recovery without double counting; budget failures (store budget, free disk) fail once without retry | implemented, tested | `test_workspace.py` |
| Streaming readers that keep decoder and parser state across chunk boundaries: UTF-8 multibyte sequences, CSV quoted newlines and escaped quotes, CRLF split across chunks, EDIFACT release characters and a UNA header split across chunks, X12 segments, XML start tags with comments, CDATA, processing instructions and quoted `>` inside attributes | implemented, tested (every chunk size from 1 upward against the standard library parser and the whole-file locators) | `test_workspace.py` |
| Staged generations: candidate memberships, comparison with the published fleet within the declared coverage scope, completeness gates (declared count, parse completion, quarantine), empty-snapshot decision, retirement withheld for partial or quarantined snapshots and confined to the snapshot's scope, members moving between scopes, explicit removal, change manifest hash, atomic publication with baseline check, replay by operation id, two publishers cannot both win, abandon | implemented, tested | `test_workspace.py`, `bench/` runs |
| Storage-backed selection manifests with expected-count check, job-scoped operation ids, reopening rejected rows, approval bound to the change-set fingerprint, optimistic version check per member, export refused on drift | implemented, tested | `test_workspace.py` |
| Re-analysis: the kernel runs again over every stored observation under the current operator policy, rows that changed are rewritten, decisions reset, memberships re-staged, gates re-evaluated, earlier approvals stale; refused once the generation is published | implemented, tested | `test_workspace.py` |
| Streamed corrected export with per-edit source verification, temporary file and rename, exceptions and ledger CSV, manifest JSON, artifact row switched to ready only after the rename, earlier artifacts superseded with lineage, interrupted build never served, retry by idempotency key, free-disk checks before and during the build | implemented, tested; output byte-identical to the whole-file correctors for CSV, EDIFACT, X12 and container XML | `test_workspace.py` |
| Versioned workspace API under `/v1/workspaces/{workspace}` (resumable uploads, sources, intents, jobs, cursor-paginated observations and findings, change set, decisions, approvals, re-analysis, exports, artifacts and files, generations, members, fleet, usage, purge, roles), admin-gated, workspace-scoped lookups, contract-shaped documents validated against `contracts/entities.schema.json` | implemented, tested | `test_workspace.py` |
| Roles per workspace (viewer, analyst, reviewer, administrator) enforced on every route; a workspace with no assignments treats every admin-session user as administrator | implemented, tested | `test_workspace.py` |
| Retention: per-workspace retention days, purge of abandoned uploads, superseded and failed artifact files, expired jobs and orphaned sources; published generations, the fleet and the audit log are kept | implemented, tested | `test_workspace.py` |
| Storage budget per workspace and minimum free disk space (`CHECKDIGIT_WORKSPACE_MAX_BYTES`, `CHECKDIGIT_WORKSPACE_MIN_FREE_BYTES`); uploads refused with 413, jobs and exports fail with `RESOURCE_BUDGET_EXCEEDED` and no partial artifact | implemented, tested (free-space threshold simulated; an actual full disk was not recorded) | `test_workspace.py` |
| Three-million-record fleet path (CSV) and one-million-record EDIFACT and container XML paths | measured; see `BENCHMARK.md` for the recorded runs, the restart, cancellation, concurrent-publisher, two-worker-process and interrupted-export evidence and the independent verification | `workspace/bench.py` |
| Workspace user interface, terminal profiles, message lifecycle, delivery and receiver feedback | not built | Phases D and E |

## Format catalogue

Columns: detect (recognize by content), inspect (locate identifiers), validate (message
syntax/schema/partner rules), edit (surgical correction), export (write output), re-import
(output reparses and revalidates). "Local" means the browser inspector; "service" means
the Python path.

| Format | Detect | Inspect | Validate | Edit | Export | Re-import | Local | Service | Notes |
|---|---|---|---|---|---|---|---|---|---|
| Plain text | yes (catch-all) | yes | no | yes | yes | yes | yes | yes, streamed | uppercase `[A-Z]{4}[0-9]{7}` only in files; free text is flagged unless trusted |
| CSV / TSV | opt-in mapping | yes | contract checks only | yes | yes | yes | yes | yes, streamed | RFC 4180 quoting, sniffed delimiter; leading zeros preserved as text. Whole-file path locates bare 4+7 tokens only; the workspace path evaluates every cell of a mapped column (kernel normalization of spaces and hyphens, whole trimmed cell as the splice span) |
| Fixed-width | opt-in ranges | yes | no | yes | yes | yes | yes | yes | 1-based inclusive ranges; whole-file parse |
| XLSX | yes | yes | no | yes | yes (ZIP re-assembled) | yes | no | yes | formula-cached values and split runs flagged; timestamps not preserved |
| JSON / JSONL | no | no | no | no | no | no | no | no | not supported |
| Generic XML | only recognized container XML | no | no | no | no | no | no | no | other XML is refused, not scanned |
| Navis N4 SNX / container XML | yes (structural) | yes | no | yes | yes | yes | yes | yes, streamed | DOCTYPE and ENTITY refused on both paths. Whole-file path parses and cross-checks located counts; the streaming path scans start tags without verifying well-formedness, and each id-bearing attribute is its own proposal (approve all occurrences of an identifier to keep them in sync) |
| UN/EDIFACT | yes | yes (EQD/C237, qualifier CN) | no | yes | yes | yes | yes | yes, streamed | no MIG validation, no envelope or count checks; the workspace path labels observations by message and segment |
| ANSI X12 | yes | yes (N7, N9*EQ) | no | yes | yes | yes | yes | yes, streamed | absent N7-18 flagged; an empty slot insertion lengthens the file by one byte; workspace tokens keep the split visible as `initial*number*check` |
| ZIP batch | yes | per member | n/a | per member | yes | per member | no | yes | budgets in `limits.py`; nested archives never expanded |
| GZIP, XLS/XLSB/ODS, PDF, images | rejected loudly | no | no | no | no | no | no | no | convert to a supported format first |

Excel worksheets hold at most 1,048,576 rows; a three-million-record export is written
as CSV by the workspace path. "Streamed" means the workspace job reads the file in
fixed-size chunks and never holds it in memory; the whole-file path stays behind the
5 MiB upload limit of `/correct`.

## Terminal systems

No terminal system profile is verified. Navis N4 SNX handling is fixture-tested on
anonymized samples only. Tideworks, CyberLogitec OPUS and RBS TOPS have no adapters,
profiles or fixtures in this repository. Nothing here demonstrates receiver acceptance.

## Integrations

| Candidate | State |
|---|---|
| BIC prefix / facility APIs, BoxTech | adapter code for BoxTech exists; not provider-tested; storage policy restricts retention; the reference library links to the official sites |
| UN/LOCODE, SMDG, NMFTA SCAC | no imports; described in the reference library |
| Maersk, CMA CGM, Hapag-Lloyd, ZIM (DCSA-style) | adapter code exists; not provider-tested |
| APM Terminals, Portbase, Terminal49, Vizion, project44 | Terminal49 adapter stub only; others none |
| BAPLIE Viewer / TEDIVO | none; benchmark only |
