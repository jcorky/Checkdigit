# Safe defaults

These defaults apply whenever a partner's behaviour is unknown. They preserve safe
inspection and block only the effects whose meaning cannot be established. Each rule
names the finding code raised when it applies (`findings.json`) and the enumeration it
draws on (`enums.json`). Nothing here is implemented as a write path yet; Phase B and C
build the workflows on these contracts.

## Import intent

- A job with no bound import intent runs in `comparison_only` mode. Findings and
  proposed differences are produced; no master data or operational state changes.
  Finding: `IMPORT_INTENT_UNRESOLVED`.
- Filename, extension and record count never infer intent. Scope is explicit
  (`coverage_scope`); a filtered extract is not a terminal-wide snapshot.
- `full_snapshot` may propose retirement only for the source's own declared membership
  within its scope, and only after completeness checks pass. Otherwise
  `SNAPSHOT_INCOMPLETE` blocks publication while diagnostics continue.
- A zero-record snapshot empties its scope only through an explicit reviewed decision
  (`EMPTY_SNAPSHOT_DECISION_REQUIRED`).
- `incremental` leaves unmentioned records and unsupplied fields unchanged.
- `explicit_removal` touches only the identified memberships or associations. Removal is
  a versioned membership change, never a physical or operational action.
- Field semantics: absent means no update; explicit null clears only where the profile
  permits; empty strings carry a separately declared meaning; zero, false, blank, null and
  omitted stay distinct. Unknown semantics block the affected update
  (`field_update_policy: unknown_blocks`).
- Publication requires the pinned baseline to still be current (`BASELINE_CONFLICT`).
  Replaying an applied intent produces no second effect (`DUPLICATE_APPLICATION_PREVENTED`).
- With no configured change-volume limit, every proposed retirement requires review.

## Feed layout changes

- The approved mapping contract and the observed structural fingerprint are stored
  separately. Raw file hashes are never used to detect layout change.
- Named-column reorder continues (`MAPPING_REORDER_HARMLESS`). Reordered positional
  columns, duplicate headers or a renamed required column block the dependent
  transformation (`MAPPING_DRIFT`). New optional fields are preserved
  (`MAPPING_EXTENSION_PRESERVED`). XML paths resolve by namespace URI; a changed URI,
  mandatory path, unit, date convention or schema version blocks (`MAPPING_DRIFT`).
- Sampling may propose a mapping; the whole stream is validated against the selected
  contract. A late violation prevents any completeness claim (`MAPPING_VIOLATION_LATE`).
- Ambiguous date formats, units inferred from magnitude, and stripped namespaces are
  never guessed. A mapping change creates a new immutable profile version and a new
  analysis; earlier jobs are not rewritten.

## Message lifecycle

- Transport envelopes, messages and business transactions are separate identities.
- Each profile defines both a business-transaction key and a message-revision key that
  include workspace, authorized partner context and business scope. An equipment number
  alone is never a key.
- Same revision key and matching payload digest: duplicate (`MESSAGE_DUPLICATE`), effect
  applied once. Same revision key, different payload: `MESSAGE_ID_CONFLICT`, nothing
  applied automatically.
- Ordering follows the partner's authoritative sequence rules only. Arrival time, lexical
  reference order and filename order are never substitutes. An older revision after a
  newer one is retained without replacing the current version (`REVISION_OUT_OF_ORDER`).
- A change, replacement or cancellation whose predecessor is missing or ambiguous is held
  (`PREDECESSOR_UNRESOLVED`). Unsupported functions are preserved and blocked
  (`lifecycle_resolution: blocked_unsupported`).
- Sender and receiver values read from a file are not authenticated
  (`sender_validated: false`) until matched to the configured connection or reviewed
  import context.
- A check-digit repair never changes a message's function, references or revision.

## Approvals

- An approval binds one change-set fingerprint. A change to before/after values, the
  affected set, import mode, scope, destination profile, applicable rule or supporting
  evidence invalidates it (`APPROVAL_STALE`). A comment or a theme change does not.
- Bulk approval freezes a storage-backed selection manifest; a live filter is never
  re-executed to approve new rows (`SELECTION_DRIFT`).
- Linked edits publish atomically; one failed precondition publishes nothing
  (`LINKED_EDIT_PRECONDITION_FAILED`). Stale version checks return a conflict, never
  last-write-wins. Retries are idempotent per operation id.
- Approving a draft is separate from authorizing transmission (`authorization_scope`).

## Equipment, visits, movements and events

- A container number is an observed identifier; equipment identities carry versioned
  assertions with provenance. A checksum candidate never merges identities.
- Visits are not merged on container number alone. Missing references are unknown, never
  empty-string keys. Ambiguous matches stay unresolved (`CONTEXT_AMBIGUOUS`).
- Load-specific mass, VGM, reefer settings, seals and full/empty state live in the visit,
  movement, cargo or event context, not on the equipment.
- Planned, estimated, actual and requested events are separate classifiers. Raw
  timestamps keep their offset and precision; a timezone-less time stays unresolved. A
  later estimate never displaces an actual. Provider assertions are kept side by side.

## Export and receiver feedback

- Artifact, delivery, technical acknowledgment and business processing are independent
  dimensions. A download proves nothing about delivery or acceptance. Silence never
  becomes acceptance.
- Feedback correlation is exact, ambiguous or unmatched; the last two go to investigation
  without changing acceptance (`FEEDBACK_AMBIGUOUS`, `FEEDBACK_UNMATCHED`). Unknown codes
  stay visible and unresolved (`FEEDBACK_CODE_UNKNOWN`).
- Manual uploads and manual outcomes are labelled as such and never presented as
  connector-verified.
- A timeout is `outcome_unknown` (`DELIVERY_OUTCOME_UNKNOWN`); no automatic resend where the
  receiver's duplicate handling is unknown.
- A rejection starts a linked repair draft with full lineage. Local undo never claims to
  reverse a processed terminal transaction.
