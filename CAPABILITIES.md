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

## Public checker (static site, `src/`)

| Capability | Status | Evidence |
|---|---|---|
| Segmented eleven-cell validator with whole-number entry, paste redistribution, overflow refusal, clear, visible normalization, explicit refusal of non-identifier characters with a proposal | implemented, connected, tested | `tests/segmented.test.ts` |
| Single-number check with per-character arithmetic, Luhn steps, remainder-10 note, category notes | implemented, connected, tested | `tests/kernel.parity.test.ts`, `tests/site.test.ts` |
| One result per validation layer (structure, check digit, prefix registration, equipment record, attribute consistency, operational state, message/profile acceptance) with source, rule version and state | implemented, connected, tested | `tests/validation.test.ts`; layers beyond structure and check digit report `not_checked` in the public checker |
| Explicit scheme selection (ISO 6346, ILU, UIC) with mismatch reported as a structure failure | implemented, connected, tested | `tests/validation.test.ts` |
| Missing-check-digit calculation ("expected check digit for this body") | implemented, connected, tested | `tests/segmented.test.ts`, `tests/validation.test.ts` |
| Shareable `/check#<token>` link, copy link, copy result as text | implemented, connected | manual browser check |
| Independence from private history; zero network calls; no external resources | implemented, tested | `tests/site.test.ts`, `tests/cost.test.ts` |
| Electric Yard design tokens, light and dark, contrast at or above 4.5:1 on checked pairs | implemented, tested | `tests/site.test.ts` |
| Several-number paste, size/type lookup, reference library, generator, offline manifest/service worker | not built | Phase B |

## Python service (`checkdigit/`), not exposed by the root build

| Capability | Status | Evidence |
|---|---|---|
| Identifier kernel (ISO 6346, ILU, UIC), decision table, ASCII-only characters | implemented, tested | `test_equipment_checkdigit.py` (22), `test_phase_a.py`, 743 decision vectors shared with TypeScript |
| Offset-verified surgical substitution with overlap checks; code-point offset semantics with byte and UTF-16 converters | implemented, tested | `test_substitution.py` (7), `test_phase_a.py` |
| Content-based format detection; binary front door rejecting docx, pptx, pdf, non-xlsx ZIP, corrupt ZIP | implemented, tested | `run_acceptance.py` A6, `dispatcher.py` |
| EDIFACT EQD/C237 equipment id correction honouring UNA separators and release characters | implemented, tested (fixtures) | `run_pass*` drivers, golden `USER01_baplie_edi.CORRECTED.txt`, `L02LOADLIST.CORRECTED_lenient.txt` |
| X12 N7 split fields and qualified N9*EQ; absent N7-18 flagged, never inserted | implemented, tested (synthetic fixture) | `run_pass6.py`, `X12_synthetic.CORRECTED.edi` |
| SNX / Navis container XML linked attributes kept in sync; DOCTYPE and ENTITY refused | implemented, tested (fixtures) | `4Containers_snx_Example.CORRECTED.xml`, `COPRAR_Discharge.xml` |
| Plain text, CSV (opt-in columns), fixed-width (opt-in ranges), XLSX shared/inline strings | implemented, tested | `run_pass17.py`, `run_pass21.py`, acceptance |
| Review-only processing changes nothing (`trust=False`) | implemented, tested | `test_phase_a.py` |
| Raw observed value, normalized form, mathematical candidate and identity basis kept distinct | implemented, tested | `test_phase_a.py` |
| Ingress body-size middleware, per-route bounded reads, pasted-text cap, archive and workbook expansion budgets, time budget | implemented, tested | `test_phase_a.py`, `limits.py` |
| Bounded, indexed near-miss retrieval with provenance | implemented, tested | `test_phase_a.py` |
| Public `/check/{token}` uses arithmetic and the offline owner register only; workspace candidates behind `/workspace/nearmiss/{token}` (admin auth) | implemented, tested | `test_phase_a.py` |
| `/correct` returns encoding, offset kind, visualization and an API version | implemented, tested | `test_phase_a.py` |
| Enrichment storage policies (full payload warehousing off by default; BoxTech fields restricted, not reused) | implemented, tested | `test_phase_a.py` |
| Enrichment adapters (BoxTech, Maersk, CMA CGM, DCSA-style carriers, Terminal49) | implemented, **not provider-tested**, not production-enabled | `enrichment.py`; credentials never configured in this repository |
| SQLite history, owner and event warehouse, insights | implemented, tested (drivers) | `run_pass12`, `run_pass16`, `run_pass22` |
| Admin routes behind Google OIDC | implemented, tested (auth drivers) | `run_pass26.py`, acceptance A13 |
| ZIP batch with member budgets, distinct member identities, report and manifest | implemented, tested | `test_phase_a.py`, `run_pass21.py` |
| Watch folder / SFTP inbox | implemented, tested (smoke) | acceptance A7; not durable (no leases, retries or recovery) |
| BAPLIE and intermodal scene models and SVG rendering | implemented, not connected to the root build | `run_pass23`, `run_pass24`; the old SPA read them |
| Shared contract data (entities, enumerations, findings, safe defaults) | implemented, tested both sides | `test_contracts.py`, `tests/contracts.test.ts` |
| Durable jobs, workspaces, import intent, generations, approvals, artifacts, feedback | **contracts only**; no runtime | `contracts/` |
| Three-million-record processing | not implemented, not measured | Phase C |

## Format catalogue

Columns: detect (recognize by content), inspect (locate identifiers), validate (message
syntax/schema/partner rules), edit (surgical correction), export (write output), re-import
(output reparses and revalidates). "partial" is explained in the notes.

| Format | Detect | Inspect | Validate | Edit | Export | Re-import | Versions / profiles | Notes |
|---|---|---|---|---|---|---|---|---|
| Plain text | yes (catch-all) | yes | no | yes | yes | yes | n/a | uppercase `[A-Z]{4}[0-9]{7}` only; free text is flagged unless trusted |
| CSV / TSV | no (opt-in hint) | yes | no | yes | yes | yes | RFC 4180 quoting, sniffed delimiter | whole-file parse; no streaming; leading zeros preserved as text |
| Fixed-width | no (opt-in ranges) | yes | no | yes | yes | yes | 1-based inclusive ranges | whole-file parse; no versioned layouts yet |
| XLSX | yes (ZIP magic + workbook part) | yes | no | yes | yes (ZIP re-assembled, member bytes preserved) | yes | shared and inline strings | formula-cached values and split rich-text runs flagged; timestamps not preserved; one worksheet set |
| JSON / JSONL | no | no | no | no | no | no | | not supported |
| Generic XML | partial (only recognized container XML) | no | no | no | no | no | | non-SNX XML is refused, not scanned |
| Navis N4 SNX | yes (structural) | yes | no | yes | yes | yes | namespace-agnostic; no N4 version schemas | DOCTYPE/ENTITY refused; parsed-count integrity check |
| UN/EDIFACT | yes (UNA/UNB/UNH) | yes (EQD/C237 when EQD01 = CN) | no | yes | yes | yes | no MIG validation; no envelope/count checks | BAPLIE scene parse exists for visualization |
| ANSI X12 | yes (ISA/GS/ST with `~`) | yes (N7, N9*EQ) | no | yes | yes | yes | no transaction/version profiles | absent N7-18 flagged |
| ZIP batch | yes | per member | n/a | per member | yes (zip of outputs) | per member | budgets in `limits.py` | nested archives never expanded |
| GZIP | no | no | no | no | no | no | | not supported |
| XLS / XLSB / ODS / PDF / images | rejected loudly | no | no | no | no | no | | convert to a supported format first |

Excel worksheets hold at most 1,048,576 rows; a three-million-record export will need
partitioned files or another format when that path is built.

## Terminal systems

No terminal system profile is verified. Navis N4 SNX handling is fixture-tested on
anonymized samples only. Tideworks, CyberLogitec OPUS and RBS TOPS have no adapters,
profiles or fixtures in this repository. Nothing here demonstrates receiver acceptance.

## Integrations

| Candidate | State |
|---|---|
| BIC prefix / facility APIs, BoxTech | adapter code for BoxTech exists; not provider-tested; storage policy restricts retention |
| UN/LOCODE, SMDG, NMFTA SCAC | no imports |
| Maersk, CMA CGM, Hapag-Lloyd, ZIM (DCSA-style) | adapter code exists; not provider-tested |
| APM Terminals, Portbase, Terminal49, Vizion, project44 | Terminal49 adapter stub only; others none |
| BAPLIE Viewer / TEDIVO | none; benchmark only |
