"""
connections.py
==============
Authorized connections to partner systems and the transports behind them.

A connection is configured (draft), authorized by an administrator with a
note naming the customer authorization, verified by a connectivity test, and
then enabled. Nothing is transmitted or polled through a connection that is
not enabled. Secrets never enter the database: the configuration names the
environment variables that hold them.

Transports:
  filesystem  a directory the partner exchanges files through (shared or
              mounted drop folder); fully supported and testable on one host
  sftp        needs the optional `paramiko` package; verification reports
              its absence instead of pretending
  https       an HTTPS endpoint receiving the artifact as a POST body;
              verification and sending only run for an authorized connection

`duplicate_handling` records what the receiver does with a repeated file:
`rejects_duplicates`, `applies_duplicates` or `unknown`. It decides whether a
resend after an unknown outcome may happen without an administrator's note.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from .store import audit, new_id, now_iso, transaction

KINDS = ("filesystem", "sftp", "https")
DIRECTIONS = ("outbound", "inbound")
DUPLICATE_HANDLING = ("unknown", "rejects_duplicates", "applies_duplicates")
STATES = ("draft", "authorized", "verified", "enabled", "disabled")
SECRET_WORDS = ("password", "secret", "token", "private_key", "api_key")
CONFIG_KEYS = {
    "filesystem": {"path", "outbox", "inbox", "processed", "intent", "run_inline"},
    "sftp": {"host", "port", "username", "password_env", "key_path", "remote_dir", "inbox", "processed", "intent", "run_inline"},
    "https": {"url", "auth_header_env", "content_type", "timeout_s"},
}


class ConnectionError_(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class TransportUnavailable(Exception):
    pass


class TransportError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #

class FilesystemTransport:
    def __init__(self, config: Dict[str, Any], direction: str):
        self.path = config["path"]
        self.outbox = os.path.join(self.path, config.get("outbox", "")) if config.get("outbox") else self.path
        self.inbox = os.path.join(self.path, config.get("inbox", "")) if config.get("inbox") else self.path
        self.processed = os.path.join(self.path, config.get("processed", "processed"))
        self.direction = direction

    def verify(self) -> Dict[str, Any]:
        if not os.path.isdir(self.path):
            raise TransportError(f"directory does not exist: {self.path}")
        target = self.outbox if self.direction == "outbound" else self.inbox
        if not os.path.isdir(target):
            raise TransportError(f"{'outbox' if self.direction == 'outbound' else 'inbox'} directory does not exist: {target}")
        if self.direction == "outbound":
            probe = os.path.join(target, f".checkdigit-probe-{int(time.time() * 1000)}")
            try:
                with open(probe, "wb") as fh:
                    fh.write(b"probe")
                os.remove(probe)
            except OSError as exc:
                raise TransportError(f"outbox is not writable: {exc}")
        else:
            try:
                os.listdir(target)
            except OSError as exc:
                raise TransportError(f"inbox is not readable: {exc}")
        return {"kind": "filesystem", "path": target, "checked": "write-and-delete probe" if self.direction == "outbound" else "listing"}

    def send(self, local_path: str, remote_name: str) -> Dict[str, Any]:
        dest = os.path.join(self.outbox, remote_name)
        if os.path.exists(dest):
            raise TransportError(f"{remote_name} already exists in the outbox")
        tmp = dest + ".part"
        shutil.copyfile(local_path, tmp)
        os.replace(tmp, dest)
        h = hashlib.sha256()
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return {"kind": "filesystem", "path": dest, "size_bytes": os.path.getsize(dest), "sha256": h.hexdigest(),
                "written_at": now_iso()}

    def list_inbox(self) -> List[Tuple[str, int]]:
        out = []
        for name in sorted(os.listdir(self.inbox)):
            full = os.path.join(self.inbox, name)
            if os.path.isfile(full) and not name.startswith(".") and not name.endswith(".part"):
                out.append((name, os.path.getsize(full)))
        return out

    def fetch(self, name: str) -> str:
        return os.path.join(self.inbox, name)

    def archive(self, name: str) -> str:
        os.makedirs(self.processed, exist_ok=True)
        dest = os.path.join(self.processed, name)
        if os.path.exists(dest):
            base, ext = os.path.splitext(name)
            dest = os.path.join(self.processed, f"{base}.{int(time.time() * 1000)}{ext}")
        shutil.move(os.path.join(self.inbox, name), dest)
        return dest


class SftpTransport:
    def __init__(self, config: Dict[str, Any], direction: str):
        self.config = config
        self.direction = direction

    def _client(self):
        try:
            import paramiko  # noqa: F401
        except ModuleNotFoundError:
            raise TransportUnavailable("sftp needs the optional paramiko package (pip install paramiko)")
        raise TransportUnavailable("sftp client sessions are not exercised in this environment")

    def verify(self) -> Dict[str, Any]:
        self._client()
        return {}

    def send(self, local_path: str, remote_name: str) -> Dict[str, Any]:
        self._client()
        return {}

    def list_inbox(self) -> List[Tuple[str, int]]:
        self._client()
        return []

    def fetch(self, name: str) -> str:
        self._client()
        return ""

    def archive(self, name: str) -> str:
        self._client()
        return ""


class HttpsTransport:
    def __init__(self, config: Dict[str, Any], direction: str):
        self.url = config["url"]
        self.auth_env = config.get("auth_header_env")
        self.content_type = config.get("content_type", "application/octet-stream")
        self.timeout = float(config.get("timeout_s", 30))
        if direction != "outbound":
            raise TransportError("https connections are outbound only")

    def _headers(self) -> Dict[str, str]:
        headers = {"content-type": self.content_type}
        if self.auth_env:
            value = os.environ.get(self.auth_env)
            if not value:
                raise TransportError(f"environment variable {self.auth_env} is not set")
            headers["authorization"] = value
        return headers

    def verify(self) -> Dict[str, Any]:
        import urllib.request
        req = urllib.request.Request(self.url, method="HEAD", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return {"kind": "https", "url": self.url, "status": res.status}
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"HEAD {self.url}: {exc}")

    def send(self, local_path: str, remote_name: str) -> Dict[str, Any]:
        import urllib.request
        with open(local_path, "rb") as fh:
            data = fh.read()
        headers = self._headers()
        headers["x-filename"] = remote_name
        req = urllib.request.Request(self.url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = res.read(4096).decode("utf-8", errors="replace")
                return {"kind": "https", "url": self.url, "status": res.status, "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(), "response": body, "written_at": now_iso()}
        except TimeoutError as exc:
            raise TimeoutError(str(exc))
        except Exception as exc:  # noqa: BLE001
            if "timed out" in str(exc).lower():
                raise TimeoutError(str(exc))
            raise TransportError(f"POST {self.url}: {exc}")

    def list_inbox(self) -> List[Tuple[str, int]]:
        return []

    def fetch(self, name: str) -> str:
        raise TransportError("https connections have no inbox")

    def archive(self, name: str) -> str:
        raise TransportError("https connections have no inbox")


def transport_for(row: sqlite3.Row):
    config = json.loads(row["config_json"] or "{}")
    kind = row["kind"]
    if kind == "filesystem":
        return FilesystemTransport(config, row["direction"])
    if kind == "sftp":
        return SftpTransport(config, row["direction"])
    return HttpsTransport(config, row["direction"])


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

def _check_config(kind: str, config: Dict[str, Any]) -> None:
    unknown = set(config) - CONFIG_KEYS[kind]
    if unknown:
        raise ConnectionError_("BAD_CONFIG", f"unknown keys for {kind}: {sorted(unknown)}")
    for key, value in config.items():
        if any(w in key.lower() for w in SECRET_WORDS) and not key.endswith("_env") and not key.endswith("_path"):
            raise ConnectionError_("SECRET_IN_CONFIG", f"{key} looks like a secret; name an environment variable with {key}_env")
        if key.endswith("_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")):
            raise ConnectionError_("SECRET_IN_CONFIG", f"{key} must name an environment variable, not hold a value")
    required = {"filesystem": ("path",), "sftp": ("host", "username", "remote_dir"), "https": ("url",)}[kind]
    for r in required:
        if not config.get(r):
            raise ConnectionError_("BAD_CONFIG", f"{kind} needs {r}")
    if kind == "https" and not str(config["url"]).lower().startswith("https://") and not str(config["url"]).startswith("http://127.0.0.1") \
            and not str(config["url"]).startswith("http://localhost"):
        raise ConnectionError_("BAD_CONFIG", "https connections need an https:// URL")
    intent = config.get("intent")
    if intent is not None:
        if not isinstance(intent, dict) or intent.get("mode") not in ("comparison_only", "incremental", "full_snapshot", "explicit_removal"):
            raise ConnectionError_("BAD_CONFIG", "intent needs a mode (comparison_only, incremental, full_snapshot, explicit_removal)")


def create_connection(conn: sqlite3.Connection, workspace_id: str, actor: str, *, name: str, kind: str, direction: str,
                      config: Dict[str, Any], partner: str = "", profile_id: Optional[str] = None,
                      duplicate_handling: str = "unknown") -> Dict[str, Any]:
    if kind not in KINDS:
        raise ConnectionError_("BAD_KIND", f"one of {KINDS}")
    if direction not in DIRECTIONS:
        raise ConnectionError_("BAD_DIRECTION", f"one of {DIRECTIONS}")
    if duplicate_handling not in DUPLICATE_HANDLING:
        raise ConnectionError_("BAD_DUPLICATE_HANDLING", f"one of {DUPLICATE_HANDLING}")
    if not name.strip():
        raise ConnectionError_("BAD_CONFIG", "name is required")
    _check_config(kind, config)
    if profile_id and conn.execute("SELECT 1 FROM feed_profiles WHERE id = ? AND workspace_id = ?", (profile_id, workspace_id)).fetchone() is None:
        raise ConnectionError_("UNKNOWN_PROFILE", profile_id)
    if conn.execute("SELECT 1 FROM connections WHERE workspace_id = ? AND name = ?", (workspace_id, name)).fetchone():
        raise ConnectionError_("NAME_TAKEN", name)
    cid = new_id("con")
    ts = now_iso()
    with transaction(conn):
        conn.execute("INSERT INTO connections (id, workspace_id, name, kind, direction, partner, profile_id, config_json, "
                     "duplicate_handling, state, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'draft',?,?,?)",
                     (cid, workspace_id, name, kind, direction, partner, profile_id, json.dumps(config, sort_keys=True),
                      duplicate_handling, actor, ts, ts))
        audit(conn, workspace_id, actor, "connection.create", cid, f"{name} {kind} {direction}")
    return connection_doc(conn, cid)


def connection_row(conn: sqlite3.Connection, connection_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    if row is None:
        raise ConnectionError_("UNKNOWN_CONNECTION", connection_id)
    return row


def connection_doc(conn: sqlite3.Connection, connection_id: str) -> Dict[str, Any]:
    row = connection_row(conn, connection_id)
    out = dict(row)
    out["config"] = json.loads(out.pop("config_json") or "{}")
    out["verification"] = json.loads(out.pop("verification_json") or "{}")
    out["authorized"] = bool(row["authorized_by"])
    out["can_transmit"] = row["state"] == "enabled" and row["direction"] == "outbound"
    out["can_poll"] = row["state"] == "enabled" and row["direction"] == "inbound"
    return out


def list_connections(conn: sqlite3.Connection, workspace_id: str) -> List[Dict[str, Any]]:
    return [connection_doc(conn, r["id"]) for r in conn.execute("SELECT id FROM connections WHERE workspace_id = ? ORDER BY name", (workspace_id,))]


def authorize(conn: sqlite3.Connection, connection_id: str, actor: str, note: str) -> Dict[str, Any]:
    """An administrator records the customer's authorization for this connection."""
    row = connection_row(conn, connection_id)
    if len(note.strip()) < 8:
        raise ConnectionError_("EVIDENCE_REQUIRED", "name the customer authorization (contract, ticket, date) in the note")
    with transaction(conn):
        conn.execute("UPDATE connections SET authorized_by = ?, authorized_at = ?, authorization_note = ?, "
                     "state = CASE WHEN state = 'draft' THEN 'authorized' ELSE state END, updated_at = ? WHERE id = ?",
                     (actor, now_iso(), note, now_iso(), connection_id))
        audit(conn, row["workspace_id"], actor, "connection.authorize", connection_id, note)
    return connection_doc(conn, connection_id)


def verify(conn: sqlite3.Connection, connection_id: str, actor: str) -> Dict[str, Any]:
    """Connectivity test. Network transports run only once the connection is authorized."""
    row = connection_row(conn, connection_id)
    if row["kind"] != "filesystem" and not row["authorized_by"]:
        raise ConnectionError_("NOT_AUTHORIZED", "a network connection is only contacted once authorized")
    try:
        result = transport_for(row).verify()
        ok, detail = True, result
    except TransportUnavailable as exc:
        ok, detail = False, {"unavailable": str(exc)}
    except TransportError as exc:
        ok, detail = False, {"error": str(exc)}
    with transaction(conn):
        conn.execute("UPDATE connections SET verified_at = ?, verification_json = ?, state = CASE WHEN ? AND state IN "
                     "('authorized','verified') THEN 'verified' WHEN ? = 0 AND state = 'verified' THEN 'authorized' ELSE state END, "
                     "updated_at = ? WHERE id = ?",
                     (now_iso() if ok else None, json.dumps({"ok": ok, "at": now_iso(), **detail}, sort_keys=True), 1 if ok else 0,
                      1 if ok else 0, now_iso(), connection_id))
        audit(conn, row["workspace_id"], actor, "connection.verify", connection_id, f"ok={ok} {json.dumps(detail, sort_keys=True)[:300]}")
    return {"connection_id": connection_id, "ok": ok, **detail, "connection": connection_doc(conn, connection_id)}


def enable(conn: sqlite3.Connection, connection_id: str, actor: str) -> Dict[str, Any]:
    """Enable requires authorization, a passed verification and, outbound, a production-enabled profile."""
    row = connection_row(conn, connection_id)
    if not row["authorized_by"]:
        raise ConnectionError_("NOT_AUTHORIZED", "authorize the connection first")
    verification = json.loads(row["verification_json"] or "{}")
    if not verification.get("ok"):
        raise ConnectionError_("NOT_VERIFIED", "run a passing connectivity test first")
    if row["direction"] == "outbound":
        if not row["profile_id"]:
            raise ConnectionError_("PROFILE_REQUIRED", "an outbound connection names the receiver's profile")
        prof = conn.execute("SELECT verification_state, name, version FROM feed_profiles WHERE id = ?", (row["profile_id"],)).fetchone()
        if prof is None or prof["verification_state"] != "production_enabled":
            raise ConnectionError_("PROFILE_UNVERIFIED", f"the receiver profile must be production_enabled "
                                   f"(is {prof['verification_state'] if prof else 'missing'})")
    with transaction(conn):
        conn.execute("UPDATE connections SET state = 'enabled', updated_at = ? WHERE id = ?", (now_iso(), connection_id))
        audit(conn, row["workspace_id"], actor, "connection.enable", connection_id)
    return connection_doc(conn, connection_id)


def disable(conn: sqlite3.Connection, connection_id: str, actor: str, reason: str = "") -> Dict[str, Any]:
    row = connection_row(conn, connection_id)
    with transaction(conn):
        conn.execute("UPDATE connections SET state = 'disabled', updated_at = ? WHERE id = ?", (now_iso(), connection_id))
        audit(conn, row["workspace_id"], actor, "connection.disable", connection_id, reason)
    return connection_doc(conn, connection_id)
