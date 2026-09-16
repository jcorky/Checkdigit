# Migration notes

Behaviour and contract changes that a consumer of the Python service or its database
must know about. Each entry names the version or phase that introduced it.

## Phase A

### API `/correct` (api_version 2)

- New top-level fields: `api_version`, `encoding` (codec used to decode the upload;
  re-encode output with it), `offset_kind` (`text_codepoint`), `visualization`
  (bay-plan or intermodal scene when drawable, otherwise `null`) and `limits`.
- `report.containers[]` gains `normalized`, `candidate` and `identity_basis`.
  `as_found` is now the raw observed token (for an X12 split field this is
  `INITIAL/NUMBER`); previously it held the normalized form. `canonical` is the working
  key: the applied correction when one was applied, otherwise the normalized token. A
  flagged token no longer reports its mathematical candidate as `canonical`.
- Malformed `columns` or `ranges` query options return 422 with the exact problem
  instead of 415 or 500.
- Uploads are bounded per route: 5 MiB per file, 24 MiB per batch archive, 1 MiB for
  pasted text (the multipart form-part cap), 32 MiB per request at ingress. Excess is
  413 (or 400 from the framework for an oversized form part).

### API `/check/{token}`

- No longer returns `near_misses`. The response carries `scope: "public_arithmetic"`
  and `history_consulted: false`. Owner information, when a register is loaded, comes
  from the offline owner register only (`owner_source`).
- Workspace candidates moved to `GET /workspace/nearmiss/{token}`, which requires the
  admin session and returns proposals with provenance.

### Service

- `service.process_upload(..., trust=False, format_hint=...)` now honours `trust`.
  Hinted CSV and fixed-width requests without `trust=True` flag failing numbers and
  change nothing. Callers that relied on the implicit trust must pass `trust=True`.
- Near-miss proposals come from bounded indexed probes (`db.candidate_neighbors`); the
  whole-fleet `db.candidate_pool` remains for small tooling only.
- Full enrichment payloads are not warehoused unless
  `CHECKDIGIT_ENRICH_PERSIST_PAYLOADS` names the source; BoxTech cannot be opted in and
  keeps only the displayed fields, which are not reused.

### Database

- `containers.identity_basis` and `event_containers.normalized`,
  `event_containers.identity_basis` are added by the idempotent migration on connect.
- Expression index `ix_containers_serial` on `substr(eqid, 5, 6)`.
- Existing rows keyed by an unapplied candidate (a flagged token's arithmetic proposal)
  are not rewritten; new sightings record under the normalized as-found key. Counters
  for such historical rows therefore stop growing.

### Kernel

- Digits are ASCII-only (`[0-9]`) in every shape check; letters in the X12 initial must
  be ASCII. Tokens with other Unicode digits or letters are `INVALID_STRUCTURE` instead of
  being computed (or, for an X12 non-ASCII initial, raising). The TypeScript port always
  behaved this way; the vectors now cover it.

### Batch

- `report.csv` keeps its column order and gains a trailing `source_name` column.
- Members are read by `ZipInfo`, so duplicate paths are distinct members; display names
  are deduplicated (`dup.txt`, `dup(2).txt`) and outputs keep the previous naming
  (`dup.corrected.txt`, `dup.corrected(2).txt`).
- Members past a budget appear in the manifest as `rejected` with the reason.
