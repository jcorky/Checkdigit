"""
store.py
========
Workspace schema, connections and migrations. SQLite in WAL mode on a single
host: the measured requirement (one operator, a few concurrent jobs, millions
of rows read by indexed queries) fits it; the schema keeps every entity of the
shared contracts (contracts/entities.schema.json) as its own table rather than
a container-number-keyed blob.

Identity rules:
  * ids are workspace-scoped text identifiers generated here;
  * a container number is an observed identifier of equipment, never a key;
  * every write path that must be idempotent has a natural unique key
    (intent fingerprint, (job, ordinal), (generation, key), (job, operation id),
    artifact idempotency key).

Budgets (environment, bytes):
  CHECKDIGIT_WORKSPACE_CACHE_KIB      SQLite page cache per connection (256 MiB)
  CHECKDIGIT_WORKSPACE_MAX_BYTES      store budget per workspace (sources, uploads,
                                      artifacts); 0 = unlimited
  CHECKDIGIT_WORKSPACE_MIN_FREE_BYTES free disk space required before and during
                                      ingest and export (1 GiB)
"""
from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import threading
import time
from typing import Dict, Iterator, List, Optional

SCHEMA_VERSION = 2

CACHE_KIB = int(os.environ.get("CHECKDIGIT_WORKSPACE_CACHE_KIB", str(256 * 1024)))
MAX_BYTES = int(os.environ.get("CHECKDIGIT_WORKSPACE_MAX_BYTES", "0"))
MIN_FREE_BYTES = int(os.environ.get("CHECKDIGIT_WORKSPACE_MIN_FREE_BYTES", str(1024 * 1024 * 1024)))

ROLE_RANK = {"viewer": 0, "analyst": 1, "reviewer": 2, "administrator": 3}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 90,
    third_party_data_retention TEXT NOT NULL DEFAULT 'displayed_fields_only',
    current_generation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    role TEXT NOT NULL,
    PRIMARY KEY (user_id, workspace_id)
);

CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    encoding TEXT,
    declared_content_type TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (workspace_id, sha256, size_bytes)
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    filename TEXT NOT NULL,
    declared_size INTEGER NOT NULL,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    source_file_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_profiles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    contract_json TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    previous_version_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, name, version)
);

CREATE TABLE IF NOT EXISTS import_intents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    mode TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    effective_time TEXT NOT NULL,
    baseline_generation_id TEXT,
    field_update_policy TEXT NOT NULL,
    change_volume_limit INTEGER,
    empty_scope_decision TEXT,
    declared_record_count INTEGER,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    profile_id TEXT,
    profile_version INTEGER,
    import_intent_id TEXT REFERENCES import_intents(id),
    state TEXT NOT NULL,
    step TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_until REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    options_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    parser_version TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    analysis_version INTEGER NOT NULL DEFAULT 1,
    generation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state, lease_until);
CREATE INDEX IF NOT EXISTS ix_jobs_workspace ON jobs(workspace_id, id);

CREATE TABLE IF NOT EXISTS job_counters (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    name TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, name)
);

-- One row per identifier observation, bound to its source location. The
-- ordinal is the job-scoped identity; it does not depend on the container number.
CREATE TABLE IF NOT EXISTS observations (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    ordinal INTEGER NOT NULL,
    record_no INTEGER NOT NULL,
    location TEXT NOT NULL,
    char_offset INTEGER NOT NULL,
    raw TEXT NOT NULL,
    normalized TEXT NOT NULL,
    scheme TEXT NOT NULL,
    status TEXT NOT NULL,
    printed_check TEXT,
    computed_check TEXT,
    candidate TEXT,
    reason TEXT NOT NULL DEFAULT '',
    attrs_hash TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'proposed',
    version INTEGER NOT NULL DEFAULT 1,
    token TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, ordinal)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_obs_status ON observations(job_id, status, ordinal);
CREATE INDEX IF NOT EXISTS ix_obs_key ON observations(job_id, normalized);
CREATE INDEX IF NOT EXISTS ix_obs_decision ON observations(job_id, decision, ordinal);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    blocking_scope TEXT NOT NULL,
    location TEXT,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_job ON findings(job_id, code, id);

CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    scope_kind TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    state TEXT NOT NULL,
    baseline_generation_id TEXT,
    import_intent_id TEXT,
    job_id TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    change_manifest_sha256 TEXT,
    change_counts_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_generations_workspace ON generations(workspace_id, id);

-- Membership of an identifier in a generation's declared scope. Staged in
-- batches; readers only consult published generations.
CREATE TABLE IF NOT EXISTS memberships (
    generation_id TEXT NOT NULL REFERENCES generations(id),
    key TEXT NOT NULL,
    attrs_hash TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'source',
    PRIMARY KEY (generation_id, key)
) WITHOUT ROWID;

-- Current published fleet state per workspace: the result of applying every
-- published generation in order. Changed only inside a publish transaction.
-- scope_kind/scope_value record the coverage scope the member was last
-- published under; a full snapshot retires only within its own scope.
CREATE TABLE IF NOT EXISTS fleet (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    key TEXT NOT NULL,
    attrs_hash TEXT NOT NULL DEFAULT '',
    generation_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL DEFAULT 'source_fleet',
    scope_value TEXT NOT NULL DEFAULT 'all',
    PRIMARY KEY (workspace_id, key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_fleet_generation ON fleet(workspace_id, generation_id);
CREATE INDEX IF NOT EXISTS ix_fleet_scope ON fleet(workspace_id, scope_kind, scope_value, key);

CREATE TABLE IF NOT EXISTS change_sets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    analysis_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL DEFAULT '1',
    edit_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    change_set_id TEXT NOT NULL REFERENCES change_sets(id),
    change_set_fingerprint TEXT NOT NULL,
    approver TEXT NOT NULL,
    authorization_scope TEXT NOT NULL DEFAULT 'draft',
    decision TEXT NOT NULL,
    state TEXT NOT NULL,
    stale_reason TEXT,
    selection_count INTEGER NOT NULL,
    selection_digest TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    operation_id TEXT,
    decided_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_approvals_operation ON approvals(job_id, operation_id);

-- Storage-backed selection manifest: exact membership frozen at approval time.
CREATE TABLE IF NOT EXISTS selection_members (
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    ordinal INTEGER NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (approval_id, ordinal)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS export_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    approval_id TEXT REFERENCES approvals(id),
    change_set_id TEXT,
    previous_artifact_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    output_mode TEXT NOT NULL DEFAULT 'surgical',
    path TEXT,
    sha256 TEXT,
    size_bytes INTEGER,
    edits_applied INTEGER NOT NULL DEFAULT 0,
    manifest_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

# Columns added after the first schema. Each entry: (table, column, definition).
MIGRATIONS = [
    ("workspaces", "third_party_data_retention", "TEXT NOT NULL DEFAULT 'displayed_fields_only'"),
    ("jobs", "policy_fingerprint", "TEXT NOT NULL DEFAULT ''"),
    ("observations", "token", "TEXT NOT NULL DEFAULT ''"),
    ("fleet", "scope_kind", "TEXT NOT NULL DEFAULT 'source_fleet'"),
    ("fleet", "scope_value", "TEXT NOT NULL DEFAULT 'all'"),
    ("approvals", "operation_id", "TEXT"),
    ("export_artifacts", "change_set_id", "TEXT"),
    ("export_artifacts", "previous_artifact_id", "TEXT"),
]

_LOCK = threading.Lock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    """Sortable, unguessable id: prefix + millisecond time + random suffix."""
    return f"{prefix}_{int(time.time() * 1000):x}{secrets.token_hex(6)}"


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_approvals_operation ON approvals(job_id, operation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_fleet_scope ON fleet(workspace_id, scope_kind, scope_value, key)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def connect(path: str, *, create: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    with _LOCK:
        for attempt in range(6):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    time.sleep(0.02 * (2 ** attempt))
                    continue
                raise
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(f"PRAGMA cache_size=-{CACHE_KIB}")
        conn.execute("PRAGMA temp_store=MEMORY")
        if create:
            conn.executescript(SCHEMA)
            _migrate(conn)
    return conn


def transaction(conn: sqlite3.Connection, immediate: bool = True) -> "_Tx":
    return _Tx(conn, immediate)


class _Tx:
    def __init__(self, conn: sqlite3.Connection, immediate: bool):
        self.conn = conn
        self.immediate = immediate

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def audit(conn: sqlite3.Connection, workspace_id: str, actor: str, action: str, subject: str, detail: str = "") -> None:
    conn.execute("INSERT INTO audit_events (workspace_id, ts, actor, action, subject, detail) VALUES (?,?,?,?,?,?)",
                 (workspace_id, now_iso(), actor, action, subject, detail))


def ensure_workspace(conn: sqlite3.Connection, workspace_id: str, name: str = "") -> None:
    conn.execute("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (?,?,?)",
                 (workspace_id, name or workspace_id, now_iso()))


def data_dir(root: str, *parts: str) -> str:
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def iter_rows(conn: sqlite3.Connection, sql: str, params=()) -> Iterator[sqlite3.Row]:
    cur = conn.execute(sql, params)
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            return
        yield from rows


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

def role_of(conn: sqlite3.Connection, workspace_id: str, user_id: str) -> Optional[str]:
    """The user's role in the workspace, or 'administrator' when the workspace
    has no role assignments at all (the server-wide admin gate is then the only
    boundary, which is how single-operator deployments run)."""
    row = conn.execute("SELECT role FROM user_roles WHERE workspace_id = ? AND user_id = ?",
                       (workspace_id, user_id)).fetchone()
    if row is not None:
        return row["role"]
    any_role = conn.execute("SELECT 1 FROM user_roles WHERE workspace_id = ? LIMIT 1", (workspace_id,)).fetchone()
    return "administrator" if any_role is None else None


def has_role(role: Optional[str], required: str) -> bool:
    return role is not None and ROLE_RANK.get(role, -1) >= ROLE_RANK[required]


def set_role(conn: sqlite3.Connection, workspace_id: str, user_id: str, role: str) -> None:
    if role not in ROLE_RANK:
        raise ValueError(f"unknown role {role!r}")
    conn.execute("INSERT INTO user_roles (user_id, workspace_id, role) VALUES (?,?,?) "
                 "ON CONFLICT(user_id, workspace_id) DO UPDATE SET role = excluded.role", (user_id, workspace_id, role))


def roles(conn: sqlite3.Connection, workspace_id: str) -> List[Dict[str, str]]:
    return [dict(r) for r in conn.execute("SELECT user_id, workspace_id, role FROM user_roles WHERE workspace_id = ? "
                                          "ORDER BY user_id", (workspace_id,))]


# --------------------------------------------------------------------------- #
# Budgets
# --------------------------------------------------------------------------- #

class BudgetExceeded(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def dir_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def usage(root: str, workspace_id: str) -> Dict[str, int]:
    out = {name: dir_bytes(os.path.join(root, name, workspace_id)) for name in ("sources", "uploads", "artifacts")}
    out["total"] = sum(out.values())
    return out


def check_store_budget(root: str, workspace_id: str, incoming_bytes: int, max_bytes: Optional[int] = None) -> None:
    limit = MAX_BYTES if max_bytes is None else max_bytes
    if limit <= 0:
        return
    used = usage(root, workspace_id)["total"]
    if used + incoming_bytes > limit:
        raise BudgetExceeded(f"workspace store would hold {used + incoming_bytes} bytes, budget is {limit}")


def check_free_space(root: str, min_free: Optional[int] = None) -> None:
    need = MIN_FREE_BYTES if min_free is None else min_free
    if need <= 0:
        return
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return
    if free < need:
        raise BudgetExceeded(f"free disk space {free} bytes is below the required {need}")
