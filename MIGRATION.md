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

## Phase C

### Workspace API (`/v1/workspaces/{workspace}/...`)

- New admin-gated routes mounted by `checkdigit/api.py`: resumable uploads
  (`POST .../uploads`, `PUT .../uploads/{id}?offset=`, `POST .../uploads/{id}/complete`),
  `POST .../intents`, `POST .../jobs` (202), `POST .../jobs/{id}/run` (inline worker),
  `POST .../jobs/{id}/cancel`, `GET .../jobs/{id}`, `GET .../jobs/{id}/observations` and
  `.../findings` (cursor pagination: `cursor`, `limit`, `next_cursor`),
  `POST .../jobs/{id}/decisions`, `GET .../approvals/{id}`, `POST .../jobs/{id}/reanalyze`,
  `POST .../jobs/{id}/exports` (201), `GET .../artifacts/{id}` and
  `.../artifacts/{id}/files/{corrected|exceptions|ledger|manifest}`,
  `GET .../generations/{id}`, `.../generations/{id}/members`,
  `POST .../generations/{id}/publish`, `GET .../fleet`, `GET .../{workspace}`.
- Conflicts answer 409 with `{"code": ..., "detail": ...}` using the shared finding codes
  (`SELECTION_DRIFT`, `APPROVAL_STALE`, `BASELINE_CONFLICT`, `PUBLICATION_BLOCKED`, ...).
  Entities from another workspace answer 404.
- Writes that must be safe to retry take `operation_id` (decisions, publish) or
  `idempotency_key` (exports) and replay the recorded outcome with `replayed: true`.

- Responses are contract-shaped (`contracts/entities.schema.json`): jobs carry `counts`,
  `dependency_pins`, `retry_count` and `partial`; generations carry `scope`; approvals
  carry `decision` as the contract verb (`approve`, `reject`, `defer`), `state` as the
  proposal state and a `selection_manifest`; artifacts carry `approval_ids`, `counts`,
  `rule_versions` and `lineage` (`sha256` only once ready); change sets list `edit_count`
  and an `edits_ref` cursor instead of inline edits. Extra operational fields are additive.
- Coverage scope kinds are the contract's: `terminal`, `source_fleet`, `location`,
  `voyage`, `profile_population` (default `source_fleet` / `all`). A full snapshot retires
  only within its own scope; members listed by another scope's snapshot move scope.
- Decision `operation_id` values are scoped to the job. A filter naming `decision`
  (for example `rejected`) reopens rows in that state.
- `POST .../jobs/{id}/reanalyze` takes `{"reason": ...}`, re-runs the kernel under the
  current policy and answers `changed`, `observations`, `analysis_version`,
  `approvals_stale`; it answers 409 `GENERATION_PUBLISHED` once the generation is
  published.
- X12 N7 observations record the split as `token = initial*number*check` (two parts when
  the check digit element is absent) and the check digit slot as the splice span.
- New routes: `GET .../sources/{id}`, `GET .../intents/{id}`, `GET .../jobs/{id}/change-set`,
  `GET .../jobs/{id}/approvals`, `GET .../jobs/{id}/artifacts`, `GET .../generations`,
  `POST .../generations/{id}/abandon`, `GET .../fleet/{key}`, `GET .../usage`,
  `POST .../maintenance/purge`, `GET|PUT|DELETE .../roles[/{user}]`, `PATCH .../{workspace}`.
- Roles: `viewer` reads, `analyst` uploads, declares intents, runs and cancels jobs and
  re-analyses, `reviewer` decides and exports, `administrator` publishes, abandons, purges,
  edits the workspace and assigns roles. 403 carries `{"code": "ROLE_REQUIRED"}`. A
  workspace with no assignments treats every admin-session user as administrator.

### Environment and storage

- `CHECKDIGIT_WORKSPACE_DIR` (default `workspace-data`) holds `workspace.db` (WAL) and
  the `sources/`, `uploads/` and `artifacts/` stores. It is separate from
  `CHECKDIGIT_DB`; nothing from the public history database is read by the workspace.
- `CHECKDIGIT_WORKSPACE_CACHE_KIB` (SQLite page cache per connection, 256 MiB),
  `CHECKDIGIT_WORKSPACE_MAX_BYTES` (store budget per workspace, 0 = unlimited) and
  `CHECKDIGIT_WORKSPACE_MIN_FREE_BYTES` (free disk required before and during ingest
  and export, 1 GiB). Budget failures answer 413 on upload creation and record
  `RESOURCE_BUDGET_EXCEEDED` on jobs and artifacts.
- Workspace jobs evaluate identifiers under the service's `CHECKDIGIT_POLICY_FILE` policy;
  the policy fingerprint is recorded on the job and in artifact manifests.
- A worker process can run `python3 -m workspace.runner <db> <dir> [--policy-file ...]
  [--exit-when-idle]`; without one, the `.../jobs/{id}/run` route executes queued work
  inside the request.
- The schema migrates in place on connect (`meta.schema_version` 2): new columns on
  `jobs`, `observations`, `fleet`, `approvals` and `export_artifacts`.

## Phase D

### Contracts

- `contracts/findings.json` 1.1.0 adds `MESSAGE_SYNTAX_INVALID`, `MESSAGE_SCHEMA_VIOLATION`
  (blocking publication), `PARTNER_RULE_VIOLATION` and `PROFILE_UNVERIFIED` (transmission).
  Both surfaces load the file; no other contract file changed.

### Workspace API additions

- Jobs accept `profile_id`; the profile version is pinned on the job. Message feeds
  without a profile still run and carry `PROFILE_UNVERIFIED`; the visit key needs the
  profile's terminal site, so without one every visit is unresolved (`CONTEXT_AMBIGUOUS`).
- Profiles: `POST|GET .../profiles`, `GET .../profiles/{id}`,
  `POST .../profiles/{id}/verify-fixtures` (analyst), `POST .../profiles/{id}/verification`
  (administrator, note required). Rules are validated on creation (`BAD_RULES`).
- Messages and context: `GET .../jobs/{id}/messages`, `.../jobs/{id}/events`,
  `.../jobs/{id}/observations/{ordinal}/layers`, `.../jobs/{id}/observations/count`,
  `.../jobs/{id}/inspection`, `GET .../visits`, `.../visits/{id}`.
- Deliveries and feedback: `POST|GET .../artifacts/{id}/deliveries`, `POST|GET .../feedback`,
  `GET .../feedback/{id}`, `POST .../feedback/{id}/repair` (202, creates a job with
  `repair_of_feedback_id`).
- Exports refuse partial linked groups with `LINKED_EDIT_PRECONDITION_FAILED`.
- Generation publication also applies the job's staged messages; a message conflict
  discovered at publish time answers 409 `MESSAGE_ID_CONFLICT` and publishes nothing.
- Re-submitting an intent whose earlier generation was abandoned creates a new job.

### Schema

- `meta.schema_version` 3: new columns on `feed_profiles` and `jobs`; new tables
  `message_transactions`, `visits`, `movements`, `events`, `observation_context`,
  `delivery_attempts`, `receiver_feedback`. Existing databases migrate on connect.

### Workspace pages

- `npm run build:workspace` builds `src/workspace/` into `checkdigit/workspace-ui/`
  (`CHECKDIGIT_WORKSPACE_UI` overrides the directory); the service serves them at
  `/workspace/`, `/workspace/job` and `/workspace/profiles` behind the admin dependency.
  They are not part of the public `dist/`.

## Phase E

### Workspace API additions

- Connections: `POST|GET .../connections`, `GET .../connections/{id}`,
  `POST .../connections/{id}/authorize` (administrator, note required), `.../verify`,
  `.../enable`, `.../disable`, `.../poll` (inbound, analyst). Configuration values that
  look like secrets are refused (`SECRET_IN_CONFIG`); keys ending in `_env` must name
  environment variables.
- Transmission: `POST .../approvals/{id}/authorize-transmission` (administrator),
  `POST .../artifacts/{id}/transmit` (201; 409 with `TRANSMISSION_NOT_AUTHORIZED`,
  `CONNECTION_NOT_ENABLED`, `PROFILE_MISMATCH`, `PROFILE_UNVERIFIED`),
  `GET .../transmissions`, `POST .../transmissions/{id}/resend` (`RESEND_NOT_SAFE` without
  a note when the receiver's duplicate handling is not `rejects_duplicates`).
- Automation: `POST .../automation/tick`, `GET .../inbox`. The worker takes
  `--automation [--automation-seconds N]` to poll enabled inbound connections and
  recover stale sends on a schedule.
- References and enrichment: `POST .../reference/owner-register` (administrator),
  `GET .../reference`, `POST|GET .../enrichment`, `POST .../enrichment/{id}/authorize`,
  `POST .../enrichment/{id}/run` (administrator), `GET .../enrichment/{id}/results`.
- Receiver feedback accepts origin `connector` (set by the inbox automation only).
- Delivery attempts carry `connection_id`, `origin`, `receipt`, `error`, `resend_of_id`
  and `resend_note`; the contract fields are unchanged.

### Schema

- `meta.schema_version` 4: new columns on `delivery_attempts`; new tables
  `connections`, `inbox_files`, `enrichment_requests`, `enrichment_results`,
  `reference_snapshots`, `owner_register`. Existing databases migrate on connect.

### Batch

- `report.csv` keeps its column order and gains a trailing `source_name` column.
- Members are read by `ZipInfo`, so duplicate paths are distinct members; display names
  are deduplicated (`dup.txt`, `dup(2).txt`) and outputs keep the previous naming
  (`dup.corrected.txt`, `dup.corrected(2).txt`).
- Members past a budget appear in the manifest as `rejected` with the reason.
