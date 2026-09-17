# Checkdigit product plan

Checkdigit is evolving from a public check-digit calculator into a container data
validation, correction and reference platform for people who work across several
terminal systems. This plan records the phases, what each depends on, and where the
work stands. `CAPABILITIES.md` records what is actually implemented, connected,
tested and deployed; this file records intent and order.

## Repository surfaces

| Surface | Location | State |
|---|---|---|
| Public static site (Vite, TypeScript) | `src/`, `tests/`, `wrangler.jsonc` | Built and tested; no backend, no paid bindings (`tests/cost.test.ts`) |
| Reference implementation and private service (Python) | `checkdigit/` | Kernel, parsers, splice, FastAPI service, SQLite history, enrichment adapters; not exposed by the root build |
| Shared contracts | `contracts/` | Schemas, enumerations, finding codes and safe defaults shared by both surfaces |

## Division of responsibility

- The identifier kernel, decision table and worked explanations run in the browser
  for the public checker and small local files. They are ported to TypeScript and
  proven against Python-generated vectors (`PARITY.md`).
- Parsing, byte-splicing, history, enrichment, durable jobs and anything touching a
  workspace or a partner system stays in Python. The browser gets a Web Worker port
  only where local processing materially helps the user (small files, no account).
- Schemas, rules, fixtures and finding codes are shared as data under `contracts/`
  and validated by both test suites, so the two surfaces cannot drift apart silently.

## Phases

### Phase A: repair, truth, foundations (this phase)

1. Reproduce and fix the reviewed defects with regression tests:
   review-only trust, public near-miss privacy, segmented-input paste, input
   normalization, `/correct` response contract, ingress size limits, ZIP/XLSX
   expansion budgets, offset semantics, raw-versus-candidate identity, bounded
   near-miss retrieval, enrichment storage controls, malformed mapping options.
2. Resolve the ASCII-digit divergence deliberately (both sides ASCII-only).
3. Publish a truthful capability map and format catalogue (`CAPABILITIES.md`).
4. Define the shared entities, enumerations, finding codes and safe defaults for
   import intent, feed contracts, message lifecycle, approvals, equipment context and
   receiver feedback (`contracts/`).
5. Define the Electric Yard design tokens and core components and apply them to the
   existing public pages.

### Phase B: local file inspector, review/export, public reference tools (delivered 2026-09-16)

Depends on: A (contracts, tokens, capability map).
Delivered: browser ports of the parsers proven against the golden files, a Web Worker
inspector with declared import intent, mapping-contract checks, proposals with evidence,
change-set-bound approvals that go stale on re-analysis, surgical export with an
exceptions file, ledger and manifest, the list checker, the initial comparison and the
reference library. Application semantics other than comparison stay comparison-only
(recorded as `IMPORT_INTENT_UNRESOLVED`). Not yet: Excel in the browser, saved mapping
recipes, offline shell.

### Phase C: private durable-job workspace and the three-million-record path (delivered 2026-09-16)

Depends on: B (review model), A (contracts).
Delivered in `checkdigit/workspace/`: SQLite-backed workspace with an immutable source
store, import intents with coverage scopes, a durable job queue (leases, heartbeats,
checkpoints, retries, cancellation, crash recovery with idempotent batches, budget
failures without retry), streaming readers for CSV, plain text, EDIFACT, X12 and
container XML that keep decoder and parser state across chunk boundaries, staged
generations with completeness gates and scope-confined retirement published atomically
onto a materialized fleet table, storage-backed selection manifests with expected-count,
job-scoped operation id and version checks, re-analysis under the current operator
policy, streamed export with verified splices, artifact supersession and free-disk checks,
roles per workspace, retention purge and storage budgets, a versioned API returning
contract-shaped documents with resumable uploads and cursor pagination, and a
reproducible benchmark with an independent verifier (`BENCHMARK.md`). SQLite stays: the
measured runs fit one host. Not in this phase: a workspace user interface, splitting one
file across several workers, an actual out-of-space recording.

### Phase D: multi-terminal profiles, EDI/XML validation, lifecycle, visits, feedback (delivered 2026-09-17)

Depends on: C (job model), partner fixtures and specifications.
Delivered: versioned feed profiles with mapping contracts, message rules and evidence-
gated verification; envelope syntax, profile schema and partner checks per message; the
message lifecycle (duplicates, conflicts, replacements, cancellations, held predecessors,
out-of-order revisions, unsupported functions) resolved after streaming and applied in
the publish transaction; visits keyed by terminal, vessel and voyage, movements and event
assertions with kept precision and offsets; seven validation layers per observation;
connected bay-plan and consist inspection; manual deliveries and receiver feedback
(CONTRL, APERAK, 997, 824, manual outcome) with exact, ambiguous or unmatched
correlation and linked repair jobs; linked edits for synced occurrences; the workspace
pages served by the service. Verification beyond `fixture_tested` needs partner fixtures
and implementation guides that are not in this repository.

### Phase E: authorized operational connections and automation

Depends on: D, customer authorization and verified receiver contracts.

## Dependency map

```
contracts/ (A) ──> local inspector + review (B) ──> durable jobs + scale (C) ──> profiles, lifecycle, feedback (D) ──> connections, automation (E)
   │                      │                              │
   └── design tokens (A) ─┴── Electric Yard on real workflows (B, C, D)
kernel + parsers (existing) ──> B, C, D
defect repairs (A) ──> everything that exposes the service layer
```

External dependencies recorded in `OPEN_ITEMS.md`: normative EN 13044-1 text, BIC
API terms and rights, partner message implementation guides and fixtures, terminal
system schemas and test data, customer authorization for any transmission.
