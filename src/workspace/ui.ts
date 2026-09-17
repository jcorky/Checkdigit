import "../styles/site.css";
import "../styles/app.css";
import "../styles/workspace.css";
import { byId, esc, html, raw } from "../ui/dom";
import { ApiError, setWorkspaceId, workspaceId } from "./api";

export { byId, esc, html, raw };

/** Header workspace switcher shared by every page. */
export function mountWorkspaceSwitch(): void {
  const el = document.getElementById("ws-switch");
  if (!el) return;
  el.innerHTML = html`<label class="inline-label">Workspace <input id="ws-id" type="text" value="${workspaceId()}" size="14" spellcheck="false"></label>
    <button id="ws-go" class="btn btn-small" type="button">Open</button>`;
  byId<HTMLButtonElement>("ws-go").addEventListener("click", () => {
    setWorkspaceId(byId<HTMLInputElement>("ws-id").value);
    location.reload();
  });
}

export function notice(target: HTMLElement, kind: "ok" | "warn" | "err" | "info", message: string): void {
  target.innerHTML = html`<div class="notice ${kind}" role="status">${message}</div>`;
}

export function showError(target: HTMLElement, err: unknown): void {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      notice(target, "err", "Admin sign-in required. Sign in on this server, then reload.");
      return;
    }
    notice(target, "err", `${err.code}: ${err.message}`);
    return;
  }
  notice(target, "err", err instanceof Error ? err.message : String(err));
}

export const STATE_KIND: Record<string, string> = {
  queued: "pending", inspecting: "pending", validating: "pending", exporting: "pending",
  awaiting_review: "warn", completed: "ok", failed: "err", cancelled: "unavail",
  candidate: "pending", publishable: "warn", published: "ok", superseded: "unavail", abandoned: "unavail",
  building: "pending", ready: "ok",
  passed: "ok", failed_layer: "err", warning: "warn", not_checked: "unavail", unavailable: "unavail", stale: "warn",
  unsupported: "unavail", insufficient_information: "unavail",
  info: "pending", blocking: "err",
  proposed: "pending", approved: "ok", rejected: "err", deferred: "warn",
  unverified: "warn", fixture_tested: "pending", partner_tested: "pending", production_enabled: "ok",
  staged: "pending", applied_scoped: "ok", held_older_revision: "warn", held_predecessor_unresolved: "err", blocked_unsupported: "err",
  exact: "ok", ambiguous: "warn", unmatched: "err",
  accepted: "ok", not_expected: "unavail", unrecognized: "warn", unknown: "unavail", partially_accepted: "warn",
  delivery_confirmed: "ok", outcome_unknown: "warn",
};

export function state(value: string): string {
  const kind = STATE_KIND[value] ?? (value === "failed" ? "err" : "unavail");
  return html`<span class="state ${kind}">${value.replace(/_/g, " ")}</span>`;
}

export function statusBadge(status: string): string {
  const kind = { valid: "ok", corrected: "warn", flagged: "warn", invalid_structure: "err", not_a_target: "unavail" }[status] ?? "unavail";
  return html`<span class="state ${kind}">${status.replace(/_/g, " ")}</span>`;
}

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("en-US");
}

export function when(ts: string | null | undefined): string {
  return ts ? ts.replace("T", " ").replace("Z", " UTC") : "—";
}

export function param(name: string): string | null {
  return new URLSearchParams(location.search).get(name);
}

export function table(headers: string[], rows: string[][], empty = "Nothing to show."): string {
  if (!rows.length) return html`<p class="small">${empty}</p>`;
  return `<div class="table-wrap"><table class="table"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
    .join("")}</tbody></table></div>`;
}

export function kv(pairs: [string, string][]): string {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

export async function run(target: HTMLElement, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (err) {
    showError(target, err);
  }
}
