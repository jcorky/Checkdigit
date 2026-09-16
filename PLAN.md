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

### Phase C: private durable-job workspace and the three-million-record path

Depends on: B (review model), A (contracts).
Durable jobs, staged snapshot/delta reconciliation, storage-backed selections,
optimistic version checks, atomic generation and artifact publication, recorded
benchmarks with restart and concurrent-update tests.

### Phase D: multi-terminal profiles, EDI/XML validation, lifecycle, visits, feedback

Depends on: C (job model), partner fixtures and specifications.
Versioned terminal profiles, syntax/schema/partner validation, profile-specific
message lifecycle, visit and movement reconciliation, connected visual inspection,
manual receiver feedback with exact correlation and linked repair.

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
