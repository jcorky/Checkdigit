import type { ApprovalRecord, Counts, Decision, Kind, PageItem, StreamOptions, StreamResult, Summary } from "../lib/largejob";
import { LARGE_PARSER_VERSION, MAX_LARGE_BYTES } from "../lib/largejob";
import { RULESET_VERSION, type ImportIntent } from "../lib/localjob";
import type { MappingContract } from "../lib/mapping";
import type {
  StreamCountResponse,
  StreamDecidedResponse,
  StreamExportedResponse,
  StreamInspectedResponse,
  StreamPageResponse,
  StreamProgressMessage,
  StreamSummaryResponse,
  StreamUndoneResponse,
  WorkerRequest,
  WorkerResponse,
} from "../worker/inspect.worker";
import { byId, html, raw } from "../ui/dom";

/*
 * Controller for files above the whole-file limit. The worker streams the
 * file and keeps proposals in the browser's private storage; this module
 * renders progress, pages through the stored records, records decisions with
 * exact counts and drives the streamed export.
 */

type Request = WorkerRequest extends infer R ? (R extends { id: number } ? Omit<R, "id"> : never) : never;
type Ask = (req: Request) => Promise<WorkerResponse>;

declare global {
  interface Window {
    showSaveFilePicker?: (options?: { suggestedName?: string }) => Promise<FileSystemFileHandle>;
  }
}

const PAGE = 200;
const KIND_LABEL: Record<Kind, string> = { corrected: "Correction", flagged: "Flagged", invalid_structure: "Invalid structure", missing: "Missing" };
const DECISION_LABEL: Record<Decision, string> = { proposed: "Undecided", approved: "Staged", rejected: "Kept original", deferred: "Deferred" };
const DECISION_BADGE: Record<Decision, string> = { proposed: "neutral", approved: "passed", rejected: "failed", deferred: "pending" };

interface LargeState {
  file: File | null;
  jobId: string;
  jobState: "uploaded" | "inspecting" | "awaiting_review" | "failed" | "exporting" | "completed" | "cancelled";
  options: StreamOptions | null;
  result: StreamResult | null;
  storage: "opfs" | "memory" | null;
  head: { bytes: number; sha256: string } | null;
  summary: Summary | null;
  decisionsDigest: string;
  approvals: ApprovalRecord[];
  filter: { kind: Kind | "all"; decision: Decision | "all" };
  items: PageItem[];
  next: number | null;
  filteredProposals: number;
  filteredCount: number;
  allowUndecided: boolean;
  startedAt: number;
  lastProgressAt: number;
  lastBytes: number;
  rate: number;
  exported: StreamExportedResponse | null;
}

function highlightChange(raw: string, candidate: string | null): string {
  if (!candidate) return "—";
  let out = "";
  for (let i = 0; i < candidate.length; i++) {
    const ch = candidate[i] as string;
    out += raw[i] === ch ? html`${ch}` : html`<span class="cd">${ch}</span>`;
  }
  return out;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[u]}`;
}

export function optionsFrom(intent: ImportIntent, mapping: MappingContract | null): StreamOptions {
  const o: StreamOptions = { owner_policy: intent.owner_policy, trust: intent.trust };
  if (intent.format_hint === "csv") {
    o.format_hint = "csv";
    o.columns = intent.parse_options.columns ?? [];
    o.has_header = intent.parse_options.has_header ?? true;
    if (mapping?.delimiter) o.delimiter = mapping.delimiter;
  }
  return o;
}

export interface LargeController {
  active(): boolean;
  /** Register a file for the streaming path; returns the decoded head so a mapping can be proposed from it. */
  setSource(file: File): Promise<string>;
  clear(): void;
  inspect(intent: ImportIntent, mapping: MappingContract | null): Promise<void>;
  progress(msg: StreamProgressMessage): void;
}

export function createLargeController(ask: Ask): LargeController {
  const state: LargeState = {
    file: null, jobId: "", jobState: "uploaded", options: null, result: null, storage: null, head: null, summary: null, decisionsDigest: "",
    approvals: [], filter: { kind: "all", decision: "all" }, items: [], next: null, filteredProposals: 0, filteredCount: 0, allowUndecided: false,
    startedAt: 0, lastProgressAt: 0, lastBytes: 0, rate: 0, exported: null,
  };
  const el = {
    results: byId("large-results"),
    state: byId("large-state"),
    progress: byId("large-progress"),
    progressNote: byId("large-progress-note"),
    cancel: byId<HTMLButtonElement>("large-cancel"),
    totals: byId("large-totals"),
    findings: byId("large-findings"),
    facets: byId("large-facets"),
    bulkScope: byId("large-bulk-scope"),
    approve: byId<HTMLButtonElement>("large-approve"),
    reject: byId<HTMLButtonElement>("large-reject"),
    defer: byId<HTMLButtonElement>("large-defer"),
    undo: byId<HTMLButtonElement>("large-undo"),
    table: byId<HTMLTableElement>("large-table"),
    empty: byId("large-empty"),
    more: byId<HTMLButtonElement>("large-more"),
    pageNote: byId("large-page-note"),
    approvals: byId("large-approvals"),
    exportSection: byId("large-export"),
    exportState: byId("large-export-state"),
    exportProgress: byId("large-export-progress"),
    build: byId<HTMLButtonElement>("large-build"),
    save: byId<HTMLButtonElement>("large-save"),
    dlCorrected: byId<HTMLAnchorElement>("large-dl-corrected"),
    dlExceptions: byId<HTMLAnchorElement>("large-dl-exceptions"),
    dlLedger: byId<HTMLAnchorElement>("large-dl-ledger"),
    dlManifest: byId<HTMLAnchorElement>("large-dl-manifest"),
    manifest: byId("large-manifest"),
    discard: byId<HTMLButtonElement>("large-discard"),
  };
  const urls: string[] = [];

  function currentFilter(): { kind?: Kind; decision?: Decision } {
    const f: { kind?: Kind; decision?: Decision } = {};
    if (state.filter.kind !== "all") f.kind = state.filter.kind;
    if (state.filter.decision !== "all") f.decision = state.filter.decision;
    return f;
  }

  function fail(message: string): void {
    state.jobState = "failed";
    renderState(message);
  }

  async function call<T extends WorkerResponse>(req: Request): Promise<T> {
    const res = await ask(req);
    if (res.type === "error") throw new Error(res.message);
    return res as T;
  }

  // ---- rendering --------------------------------------------------------------

  function renderState(error = ""): void {
    const labels: Record<LargeState["jobState"], string> = { uploaded: "Uploaded", inspecting: "Streaming…", awaiting_review: "Awaiting review", failed: "Failed", exporting: "Exporting…", completed: "Completed", cancelled: "Cancelled" };
    const cls = state.jobState === "failed" ? "failed" : state.jobState === "inspecting" || state.jobState === "exporting" ? "pending" : state.jobState === "completed" ? "passed" : state.jobState === "cancelled" ? "stale" : "neutral";
    const storage = state.storage === "opfs" ? "proposals kept in this site's private browser storage" : state.storage === "memory" ? "proposals kept in tab memory (no private storage available)" : "";
    el.state.innerHTML =
      html`<span class="badge ${cls}">${labels[state.jobState]}</span> <span class="muted small">streaming path · parser ${LARGE_PARSER_VERSION} · rules ${RULESET_VERSION}${storage ? ` · ${storage}` : ""}</span>` +
      (error ? html`<p class="notice"><span class="notice-error">${error}</span></p>` : "");
    el.cancel.hidden = state.jobState !== "inspecting" && state.jobState !== "exporting";
  }

  function renderProgress(bar: HTMLElement, done: number, total: number): void {
    bar.hidden = false;
    const inner = bar.firstElementChild as HTMLElement;
    inner.style.width = `${total ? Math.min(100, (done / total) * 100).toFixed(1) : 0}%`;
  }

  function renderProgressNote(counts: Counts | null, done: number, total: number, phase: string): void {
    const now = performance.now();
    if (now - state.lastProgressAt > 800) {
      const dt = (now - state.lastProgressAt) / 1000;
      if (state.lastProgressAt) state.rate = (done - state.lastBytes) / dt;
      state.lastProgressAt = now;
      state.lastBytes = done;
    }
    const elapsed = (now - state.startedAt) / 1000;
    const rate = state.rate > 0 ? `${fmtBytes(state.rate)}/s` : "";
    const remaining = state.rate > 0 ? Math.max(0, (total - done) / state.rate) : 0;
    const eta = state.rate > 0 && done < total ? ` · about ${Math.ceil(remaining)} s left` : "";
    const body = counts
      ? ` · ${counts.records.toLocaleString()} records · ${counts.corrected.toLocaleString()} corrections proposed · ${(counts.flagged + counts.invalid_structure + counts.missing).toLocaleString()} for review`
      : "";
    el.progressNote.textContent = `${phase}: ${fmtBytes(done)} of ${fmtBytes(total)} (${elapsed.toFixed(0)} s${rate ? `, ${rate}` : ""})${eta}${body}`;
  }

  function renderTotals(): void {
    const r = state.result;
    if (!r) {
      el.totals.innerHTML = "";
      return;
    }
    const c = r.counts;
    const tiles: [string, string | number][] = [
      ["Format", r.format],
      ["Encoding", r.encoding],
      ["Records", c.records.toLocaleString()],
      ["Identifiers", c.identifiers.toLocaleString()],
      ["Valid", c.valid.toLocaleString()],
      ["Corrections proposed", c.corrected.toLocaleString()],
      ["Flagged", c.flagged.toLocaleString()],
      ["Invalid structure", c.invalid_structure.toLocaleString()],
      ["Missing", c.missing.toLocaleString()],
      ["Not a target", c.not_a_target.toLocaleString()],
      ["Streamed in", `${(r.elapsed_ms / 1000).toFixed(1)} s`],
    ];
    el.totals.innerHTML = tiles.map(([k, v]) => html`<div class="tile"><span class="k">${k}</span><span class="v">${String(v)}</span></div>`).join("");
  }

  function renderFindings(): void {
    const notes: string[] = [];
    const r = state.result;
    if (r?.cancelled) notes.push("The pass was cancelled; counts and proposals cover only the streamed part of the file.");
    if (r && r.format === "txt" && !state.options?.trust && r.counts.flagged) {
      notes.push(`${r.counts.flagged.toLocaleString()} numbers found in free text without a declared equipment field are flagged, not corrected. Turn on "treat as declared equipment IDs" only if this file is a container list.`);
    }
    if (r && r.encoding === "latin-1") notes.push("The file is not valid UTF-8 and was read as ISO-8859-1; the export writes the same bytes back.");
    notes.push("Near-miss suggestions against the file's own valid numbers are not computed on the streaming path.");
    el.findings.innerHTML = notes.length ? `<ul class="findings">` + notes.map((n) => html`<li>${n}</li>`).join("") + `</ul>` : "";
  }

  function renderFacets(): void {
    const s = state.summary;
    if (!s) return;
    const kinds = (Object.keys(KIND_LABEL) as Kind[]).map((k) => [k, Object.values(s[k]).reduce((a, b) => a + b, 0)] as [Kind, number]);
    const decisions = (Object.keys(DECISION_LABEL) as Decision[]).map((d) => [d, (Object.keys(KIND_LABEL) as Kind[]).reduce((a, k) => a + s[k][d], 0)] as [Decision, number]);
    const total = kinds.reduce((a, [, n]) => a + n, 0);
    const group = (title: string, key: "kind" | "decision", rows: [string, number][], labels: Record<string, string>) =>
      `<div class="facet"><span class="facet-title">${title}</span>` +
      [["all", total] as [string, number], ...rows]
        .map(([v, n]) => html`<button type="button" class="facet-item ${state.filter[key] === v ? "on" : ""}" data-key="${key}" data-value="${v}">${labels[v] ?? "all"} <span class="muted">${n.toLocaleString()}</span></button>`)
        .join("") +
      "</div>";
    el.facets.innerHTML = group("Kind", "kind", kinds, KIND_LABEL) + group("Decision", "decision", decisions, DECISION_LABEL);
    el.facets.querySelectorAll<HTMLButtonElement>(".facet-item").forEach((b) =>
      b.addEventListener("click", () => {
        const key = b.dataset["key"] as "kind" | "decision";
        (state.filter as Record<string, string>)[key] = b.dataset["value"] as string;
        void reloadPage();
      }),
    );
    el.bulkScope.textContent = `Bulk actions apply to the ${state.filteredProposals.toLocaleString()} undecided or deferred proposal${state.filteredProposals === 1 ? "" : "s"} among the ${state.filteredCount.toLocaleString()} filtered rows, not only the rows on screen.`;
    const busy = state.jobState !== "awaiting_review";
    el.approve.disabled = busy || state.filteredProposals === 0;
    el.reject.disabled = busy || state.filteredProposals === 0;
    el.defer.disabled = busy || state.filteredProposals === 0;
    el.undo.disabled = busy || state.filteredCount === 0;
  }

  function renderTable(): void {
    const body = el.table.tBodies[0] as HTMLTableSectionElement;
    el.empty.hidden = state.items.length > 0;
    body.innerHTML = state.items
      .map(
        (p) => html`<tr data-index="${p.index}">
          <td class="id">${p.index + 1}</td>
          <td class="small muted">${p.location}</td>
          <td class="id">${p.raw || "—"}${p.raw_truncated ? "…" : ""}</td>
          <td class="id">${raw(highlightChange(p.raw, p.candidate))}</td>
          <td><span class="badge ${p.kind === "missing" ? "neutral" : p.kind}">${KIND_LABEL[p.kind]}</span></td>
          <td><span class="badge ${DECISION_BADGE[p.decision]}">${DECISION_LABEL[p.decision]}</span></td>
          <td class="actions">${
            p.kind === "corrected" && (p.decision === "proposed" || p.decision === "deferred")
              ? raw(html`<button type="button" class="btn btn-ghost btn-small" data-act="approved" data-index="${p.index}">Stage change</button><button type="button" class="btn btn-ghost btn-small" data-act="rejected" data-index="${p.index}">Keep original</button>${p.decision === "proposed" ? raw(html`<button type="button" class="btn btn-ghost btn-small" data-act="deferred" data-index="${p.index}">Defer</button>`) : ""}`)
              : ""
          }${p.decision !== "proposed" ? raw(html`<button type="button" class="btn btn-ghost btn-small" data-act="undo" data-index="${p.index}">Undo</button>`) : ""}</td>
        </tr>`,
      )
      .join("");
    body.querySelectorAll<HTMLButtonElement>("[data-act]").forEach((b) =>
      b.addEventListener("click", () => {
        const index = Number(b.dataset["index"]);
        const act = b.dataset["act"] as Decision | "undo";
        void decideRows({ index }, act, act === "undo" ? null : 1);
      }),
    );
    el.more.hidden = state.next === null;
    el.pageNote.textContent = state.items.length ? `${state.items.length.toLocaleString()} of ${state.filteredCount.toLocaleString()} filtered rows shown` : "";
  }

  function renderApprovals(): void {
    el.approvals.innerHTML =
      `<h3 class="sub-title">Decisions</h3>` +
      (state.approvals.length
        ? `<ul class="occ">` +
          state.approvals
            .map((a) => html`<li><span class="id">${a.id}</span><br><span class="muted small">${DECISION_LABEL[a.decision]} · ${a.count.toLocaleString()} proposal${a.count === 1 ? "" : "s"} · ${a.filter.index !== undefined ? `row ${a.filter.index + 1}` : `filter ${a.filter.kind ?? "all"} / ${a.filter.decision ?? "all"}`} · ${a.decided_at.slice(11, 19)}Z</span></li>`)
            .join("") +
          `</ul>`
        : `<p class="muted small">Each decision is recorded with the exact count it covered. A bulk action is refused if the selection changed between counting and deciding.</p>`) +
      (state.decisionsDigest ? html`<p class="muted small">Decision set digest <span class="id">${state.decisionsDigest.slice(0, 16)}…</span></p>` : "");
  }

  function renderExportState(): void {
    const s = state.summary;
    if (!s) return;
    const undecided = s.corrected.proposed;
    const deferred = s.corrected.deferred;
    const approved = s.corrected.approved;
    let msg = `${approved.toLocaleString()} staged · ${undecided.toLocaleString()} undecided · ${deferred.toLocaleString()} deferred · ${s.corrected.rejected.toLocaleString()} kept original`;
    let blocked = false;
    if (undecided + deferred > 0 && !state.allowUndecided) {
      msg += " — blocked: decide every proposal, or allow undecided items to be exported unchanged with an exception report.";
      blocked = true;
    }
    el.exportState.innerHTML = html`${msg}` + (undecided + deferred > 0 ? html` <label class="check small"><input type="checkbox" id="large-allow-undecided" ${state.allowUndecided ? "checked" : ""}> Export with undecided and deferred items unchanged (exception report attached)</label>` : "");
    document.getElementById("large-allow-undecided")?.addEventListener("change", (ev) => {
      state.allowUndecided = (ev.target as HTMLInputElement).checked;
      renderExportState();
    });
    const busy = state.jobState !== "awaiting_review" && state.jobState !== "completed";
    el.build.disabled = blocked || busy;
    el.save.hidden = typeof window.showSaveFilePicker !== "function";
    el.save.disabled = blocked || busy;
  }

  function renderAll(): void {
    renderState();
    renderTotals();
    renderFindings();
    renderFacets();
    renderTable();
    renderApprovals();
    renderExportState();
  }

  // ---- data ------------------------------------------------------------------

  async function refreshCounts(): Promise<void> {
    const c = await call<StreamCountResponse>({ type: "stream_count", filter: currentFilter() });
    state.filteredCount = c.count;
    state.filteredProposals = c.proposals;
  }

  async function reloadPage(): Promise<void> {
    await refreshCounts();
    const page = await call<StreamPageResponse>({ type: "stream_page", from: 0, n: PAGE, filter: currentFilter() });
    state.items = page.items;
    state.next = page.next;
    renderFacets();
    renderTable();
  }

  async function loadMore(): Promise<void> {
    if (state.next === null) return;
    const page = await call<StreamPageResponse>({ type: "stream_page", from: state.next, n: PAGE, filter: currentFilter() });
    state.items.push(...page.items);
    state.next = page.next;
    renderTable();
  }

  async function decideRows(filter: { index?: number; kind?: Kind; decision?: Decision }, act: Decision | "undo", expected: number | null): Promise<void> {
    try {
      if (act === "undo") {
        const res = await call<StreamUndoneResponse>({ type: "stream_undo", filter });
        state.summary = res.summary;
        state.decisionsDigest = res.decisions_digest;
      } else if (act !== "proposed") {
        const res = await call<StreamDecidedResponse>({ type: "stream_decide", filter, decision: act, expected });
        state.approvals.push(res.approval);
        state.summary = res.summary;
        state.decisionsDigest = res.decisions_digest;
      }
      await reloadPage();
      renderApprovals();
      renderExportState();
    } catch (err) {
      renderState((err as Error).message);
    }
  }

  for (const [button, act] of [
    [el.approve, "approved"],
    [el.reject, "rejected"],
    [el.defer, "deferred"],
    [el.undo, "undo"],
  ] as const) {
    button.addEventListener("click", async () => {
      await refreshCounts();
      const n = act === "undo" ? state.filteredCount : state.filteredProposals;
      if (!n) return;
      const verb = act === "approved" ? "Stage" : act === "rejected" ? "Keep the original for" : act === "deferred" ? "Defer" : "Undo decisions on";
      if (confirm(`${verb} ${n.toLocaleString()} ${act === "undo" ? "filtered row" : "proposal"}${n === 1 ? "" : "s"} matching the current filter? The action is refused if the count changes before it runs.`)) {
        void decideRows(currentFilter(), act, act === "undo" ? null : n);
      }
    });
  }
  el.more.addEventListener("click", () => void loadMore());
  el.cancel.addEventListener("click", () => void ask({ type: "stream_cancel" }));
  el.discard.addEventListener("click", async () => {
    await ask({ type: "stream_close" });
    clear();
  });

  // ---- export ----------------------------------------------------------------

  function link(a: HTMLAnchorElement, file: File | Blob, name: string): void {
    const url = URL.createObjectURL(file);
    urls.push(url);
    a.href = url;
    a.download = name;
    a.hidden = false;
  }

  async function runExport(saveHandle: FileSystemFileHandle | null): Promise<void> {
    if (!state.file || !state.result || !state.summary) return;
    state.jobState = "exporting";
    state.startedAt = performance.now();
    state.lastProgressAt = 0;
    state.lastBytes = 0;
    state.rate = 0;
    renderState();
    renderExportState();
    for (const u of urls.splice(0)) URL.revokeObjectURL(u);
    const stem = state.file.name.replace(/(\.[^.]+)?$/, "");
    const ext = /\.[^.]+$/.exec(state.file.name)?.[0] ?? ".txt";
    const outName = `${stem}.corrected${ext}`;
    let res: StreamExportedResponse;
    try {
      res = await call<StreamExportedResponse>({ type: "stream_export", filename: outName, save_handle: saveHandle });
    } catch (err) {
      state.jobState = (err as Error).message.includes("EXPORT_CANCELLED") ? "awaiting_review" : "failed";
      renderState((err as Error).message);
      el.exportProgress.hidden = true;
      renderExportState();
      return;
    }
    state.exported = res;
    const manifest = {
      artifact_version: "1",
      path: "streaming",
      source: {
        filename: state.file.name,
        size_bytes: state.file.size,
        last_modified: new Date(state.file.lastModified).toISOString(),
        head_sha256: state.head ? { bytes: state.head.bytes, sha256: state.head.sha256 } : null,
        encoding: state.result.encoding,
        immutable: true,
      },
      parser_version: state.result.parser_version,
      ruleset_version: RULESET_VERSION,
      options: state.options,
      detected_format: state.result.format,
      counts: state.result.counts,
      complete_pass: !state.result.cancelled,
      decisions: { digest: res.decisions_digest, summary: state.summary, approvals: state.approvals },
      output: {
        filename: saveHandle ? "written to the location chosen in the save dialog" : outName,
        size_bytes: res.result.bytes_written,
        digest: res.result.sha256,
        encoding: state.result.encoding,
        output_mode: "surgical",
        edits_applied: res.result.edits_applied,
      },
      exceptions: res.exception_rows,
      ledger_rows: res.ledger_rows,
      unresolved: state.summary.corrected.proposed + state.summary.corrected.deferred,
      built_at: new Date().toISOString(),
      verification: {
        spliced_spans_verified: true,
        note: "every approved edit was checked against the source bytes at its recorded offset before splicing; the output was not reparsed",
      },
    };
    if (res.corrected) link(el.dlCorrected, res.corrected, outName);
    else el.dlCorrected.hidden = true;
    link(el.dlExceptions, res.exceptions, `${stem}.exceptions.csv`);
    link(el.dlLedger, res.ledger, `${stem}.ledger.csv`);
    const manifestText = JSON.stringify(manifest, null, 2);
    link(el.dlManifest, new Blob([manifestText], { type: "application/json" }), `${stem}.manifest.json`);
    el.manifest.hidden = false;
    el.manifest.textContent = manifestText;
    el.exportProgress.hidden = true;
    state.jobState = "completed";
    renderState();
    renderExportState();
    el.exportState.innerHTML = html`Artifact ready: <span class="id">${outName}</span> · ${res.result.bytes_written.toLocaleString()} bytes · digest <span class="id">${res.result.sha256.slice(0, 23)}…</span> · ${res.result.edits_applied.toLocaleString()} edit${res.result.edits_applied === 1 ? "" : "s"} applied · ${res.exception_rows.toLocaleString()} exception row${res.exception_rows === 1 ? "" : "s"}${saveHandle ? " · corrected file written to the chosen location" : ""}. A downloaded file is not the same as a successful import into a TOS.`;
  }

  el.build.addEventListener("click", () => void runExport(null));
  el.save.addEventListener("click", async () => {
    if (!state.file || typeof window.showSaveFilePicker !== "function") return;
    const stem = state.file.name.replace(/(\.[^.]+)?$/, "");
    const ext = /\.[^.]+$/.exec(state.file.name)?.[0] ?? ".txt";
    let handle: FileSystemFileHandle;
    try {
      handle = await window.showSaveFilePicker({ suggestedName: `${stem}.corrected${ext}` });
    } catch {
      return;
    }
    await runExport(handle);
  });

  // ---- lifecycle -------------------------------------------------------------

  function clear(): void {
    state.file = null;
    state.result = null;
    state.summary = null;
    state.options = null;
    state.approvals = [];
    state.items = [];
    state.next = null;
    state.exported = null;
    state.jobState = "uploaded";
    state.filter = { kind: "all", decision: "all" };
    for (const u of urls.splice(0)) URL.revokeObjectURL(u);
    el.results.hidden = true;
    el.exportSection.hidden = true;
    el.manifest.hidden = true;
    for (const a of [el.dlCorrected, el.dlExceptions, el.dlLedger, el.dlManifest]) a.hidden = true;
  }

  return {
    active: () => state.file !== null,
    async setSource(file) {
      clear();
      if (file.size > MAX_LARGE_BYTES) throw new Error(`${file.name} is ${fmtBytes(file.size)}; the streaming inspector handles files up to ${fmtBytes(MAX_LARGE_BYTES)}`);
      state.file = file;
      state.jobId = `job_${Date.now().toString(36)}`;
      const headBytes = new Uint8Array(await file.slice(0, 256 * 1024).arrayBuffer());
      let head = new TextDecoder("utf-8", { fatal: false }).decode(headBytes);
      const lastBreak = head.lastIndexOf("\n");
      if (lastBreak > 0) head = head.slice(0, lastBreak + 1);
      return head;
    },
    clear,
    async inspect(intent, mapping) {
      if (!state.file) return;
      if (intent.format_hint === "fixed") {
        el.results.hidden = false;
        fail("Fixed-width column ranges are not supported on the streaming path. Files up to 5 MiB can use them; larger files need a delimited or message format.");
        return;
      }
      state.options = optionsFrom(intent, mapping);
      state.result = null;
      state.summary = null;
      state.approvals = [];
      state.items = [];
      state.jobState = "inspecting";
      state.startedAt = performance.now();
      state.lastProgressAt = 0;
      state.lastBytes = 0;
      state.rate = 0;
      el.results.hidden = false;
      el.exportSection.hidden = true;
      el.totals.innerHTML = "";
      el.findings.innerHTML = "";
      el.facets.innerHTML = "";
      (el.table.tBodies[0] as HTMLTableSectionElement).innerHTML = "";
      renderState();
      renderProgress(el.progress, 0, state.file.size);
      renderProgressNote(null, 0, state.file.size, "streaming");
      let res: StreamInspectedResponse;
      try {
        res = await call<StreamInspectedResponse>({ type: "stream_inspect", file: state.file, options: state.options, job_id: state.jobId });
      } catch (err) {
        el.progress.hidden = true;
        fail((err as Error).message);
        return;
      }
      state.result = res.result;
      state.storage = res.storage;
      state.summary = res.summary;
      state.head = { bytes: res.head_bytes, sha256: res.head_sha256 };
      state.jobState = res.result.cancelled ? "cancelled" : "awaiting_review";
      renderProgress(el.progress, res.result.counts.bytes_done, res.result.counts.bytes_total);
      renderProgressNote(res.result.counts, res.result.counts.bytes_done, res.result.counts.bytes_total, res.result.cancelled ? "cancelled" : "done");
      await reloadPage();
      renderAll();
      el.exportSection.hidden = false;
    },
    progress(msg) {
      if (msg.phase === "exporting") {
        renderProgress(el.exportProgress, msg.bytes_done, msg.bytes_total);
        el.exportState.textContent = "";
        renderProgressNote(null, msg.bytes_done, msg.bytes_total, "exporting");
        return;
      }
      renderProgress(el.progress, msg.bytes_done, msg.bytes_total);
      renderProgressNote(msg.counts, msg.bytes_done, msg.bytes_total, msg.phase);
    },
  };
}
