import "../styles/site.css";
import "../styles/app.css";
import { sha256Hex, type Codec } from "../lib/encoding";
import {
  applyApproved,
  changeSetFingerprint,
  decide,
  defaultIntent,
  exceptionsCsv,
  ledgerCsv,
  markStale,
  type Analysis,
  type Approval,
  type ImportIntent,
  type Manifest,
  type Proposal,
  type SourceFile,
} from "../lib/localjob";
import { proposeContract, type MappingContract } from "../lib/mapping";
import { decodeBytes } from "../lib/encoding";
import { WHOLE_FILE_LIMIT } from "../lib/largejob";
import type { ApplyResponse, InspectResponse, StreamProgressMessage, WorkerRequest, WorkerResponse } from "../worker/inspect.worker";
import { byId, html, raw } from "../ui/dom";
import { createLargeController } from "./files-large";

// The Files page: bind the local job model to the DOM. Parsing and splicing
// happen in the worker; approvals and exports are built from the model here.
// Files above WHOLE_FILE_LIMIT are handed to the streaming controller.

const worker = new Worker(new URL("../worker/inspect.worker.ts", import.meta.url), { type: "module" });
let requestSeq = 0;
const pending = new Map<number, (r: WorkerResponse) => void>();
worker.onmessage = (ev: MessageEvent<WorkerResponse>) => {
  if (ev.data.type === "stream_progress") {
    large.progress(ev.data as StreamProgressMessage);
    return;
  }
  const cb = pending.get(ev.data.id);
  if (cb) {
    pending.delete(ev.data.id);
    cb(ev.data);
  }
};
type Request = WorkerRequest extends infer R ? (R extends { id: number } ? Omit<R, "id"> : never) : never;
function ask(req: Request, transfer: Transferable[] = []): Promise<WorkerResponse> {
  const id = ++requestSeq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    worker.postMessage({ ...req, id }, transfer);
  });
}

interface State {
  source: { bytes: Uint8Array; filename: string; content_type: string } | null;
  sourceFile: SourceFile | null;
  text: string | null;
  encoding: Codec | null;
  intent: ImportIntent;
  mapping: MappingContract | null;
  analysis: Analysis | null;
  proposals: Proposal[];
  approvals: Approval[];
  fingerprint: string;
  filter: { kind: string; state: string; issue: string };
  selected: string | null;
  jobState: string;
  exportState: string;
  allowUndecided: boolean;
}

const state: State = {
  source: null,
  sourceFile: null,
  text: null,
  encoding: null,
  intent: defaultIntent(),
  mapping: null,
  analysis: null,
  proposals: [],
  approvals: [],
  fingerprint: "",
  filter: { kind: "all", state: "all", issue: "all" },
  selected: null,
  jobState: "uploaded",
  exportState: "",
  allowUndecided: false,
};

const el = {
  drop: byId("drop"),
  file: byId<HTMLInputElement>("file"),
  paste: byId<HTMLTextAreaElement>("paste"),
  usePaste: byId<HTMLButtonElement>("use-paste"),
  sourceLine: byId("source-line"),
  mode: byId<HTMLSelectElement>("mode"),
  scopeKind: byId<HTMLSelectElement>("scope-kind"),
  scopeValue: byId<HTMLInputElement>("scope-value"),
  formatHint: byId<HTMLSelectElement>("format-hint"),
  ownerPolicy: byId<HTMLSelectElement>("owner-policy"),
  trust: byId<HTMLInputElement>("trust"),
  mapping: byId("mapping"),
  mappingTable: byId<HTMLTableElement>("mapping-table"),
  fixed: byId("fixed"),
  ranges: byId<HTMLInputElement>("ranges"),
  headerLines: byId<HTMLInputElement>("header-lines"),
  inspect: byId<HTMLButtonElement>("inspect"),
  inspectNote: byId("inspect-note"),
  results: byId("results"),
  jobState: byId("job-state"),
  totals: byId("totals"),
  findings: byId("findings"),
  facets: byId("facets"),
  bulkScope: byId("bulk-scope"),
  bulkApprove: byId<HTMLButtonElement>("bulk-approve"),
  bulkReject: byId<HTMLButtonElement>("bulk-reject"),
  bulkUndo: byId<HTMLButtonElement>("bulk-undo"),
  proposals: byId<HTMLTableElement>("proposals"),
  proposalsEmpty: byId("proposals-empty"),
  proposalsEmptyText: byId("proposals-empty-text"),
  evidence: byId("evidence"),
  exportSection: byId("export"),
  exportState: byId("export-state"),
  build: byId<HTMLButtonElement>("build"),
  dlExtras: byId("dl-extras"),
  dlCorrected: byId<HTMLAnchorElement>("dl-corrected"),
  dlExceptions: byId<HTMLAnchorElement>("dl-exceptions"),
  dlLedger: byId<HTMLAnchorElement>("dl-ledger"),
  dlManifest: byId<HTMLAnchorElement>("dl-manifest"),
  manifestPreview: byId("manifest-preview"),
};

// ---- step 1: source ---------------------------------------------------------

const large = createLargeController(ask);

async function chooseFile(f: File): Promise<void> {
  if (f.size > WHOLE_FILE_LIMIT) {
    await setLargeSource(f);
    return;
  }
  await setSource(new Uint8Array(await f.arrayBuffer()), f.name, f.type);
}

async function setLargeSource(f: File): Promise<void> {
  let head: string;
  try {
    head = await large.setSource(f);
  } catch (err) {
    el.sourceLine.hidden = false;
    el.sourceLine.innerHTML = html`<span class="notice-error">${(err as Error).message}</span>`;
    return;
  }
  state.source = null;
  state.sourceFile = null;
  state.text = head;
  state.encoding = null;
  state.mapping = null;
  state.analysis = null;
  state.proposals = [];
  state.jobState = "uploaded";
  el.sourceLine.hidden = false;
  el.sourceLine.innerHTML = html`<span class="id">${f.name}</span> · ${f.size.toLocaleString()} bytes · streaming path (above ${(WHOLE_FILE_LIMIT / 1024 / 1024).toFixed(0)} MiB) · read from disk in this tab only`;
  el.inspect.disabled = false;
  el.results.hidden = true;
  el.exportSection.hidden = true;
  refreshMapping();
}

async function setSource(bytes: Uint8Array, filename: string, contentType: string): Promise<void> {
  large.clear();
  state.source = { bytes, filename, content_type: contentType };
  const sha = await sha256Hex(bytes);
  state.sourceFile = {
    filename,
    size_bytes: bytes.length,
    sha256: sha,
    encoding: "",
    declared_content_type: contentType,
    received_at: new Date().toISOString(),
    immutable: true,
  };
  const { text, encoding } = decodeBytes(bytes);
  state.text = text;
  state.encoding = encoding;
  state.sourceFile.encoding = encoding;
  el.sourceLine.hidden = false;
  el.sourceLine.innerHTML = html`<span class="id">${filename}</span> · ${bytes.length.toLocaleString()} bytes · ${encoding} · sha256 <span class="id">${sha.slice(0, 16)}…</span> · read in this tab only`;
  el.inspect.disabled = false;
  // A new source starts a fresh review: the mapping is re-derived from this
  // file's own header, and prior decisions and artefacts do not carry over.
  state.mapping = null;
  state.analysis = null;
  state.proposals = [];
  state.approvals = [];
  state.selected = null;
  state.fingerprint = "";
  state.allowUndecided = false;
  state.jobState = "uploaded";
  el.results.hidden = true;
  el.exportSection.hidden = true;
  el.dlExtras.hidden = true;
  refreshMapping();
}

el.file.addEventListener("change", async () => {
  const f = el.file.files?.[0];
  if (!f) return;
  await chooseFile(f);
});
el.drop.addEventListener("click", () => el.file.click());
el.drop.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    el.file.click();
  }
});
el.drop.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  el.drop.classList.add("over");
});
el.drop.addEventListener("dragleave", () => el.drop.classList.remove("over"));
el.drop.addEventListener("drop", async (ev) => {
  ev.preventDefault();
  el.drop.classList.remove("over");
  const f = ev.dataTransfer?.files?.[0];
  if (f) await chooseFile(f);
});
el.usePaste.addEventListener("click", async () => {
  const text = el.paste.value;
  if (!text.trim()) return;
  await setSource(new TextEncoder().encode(text), "pasted.txt", "text/plain");
});

// ---- step 2: intent + mapping ----------------------------------------------

function readIntent(): ImportIntent {
  const parse: ImportIntent["parse_options"] = {};
  if (el.formatHint.value === "csv" && state.mapping) {
    const cols = state.mapping.fields.filter((f) => f.type === "identifier").map((f) => f.identity);
    parse.columns = cols;
    parse.has_header = state.mapping.has_header;
  } else if (el.formatHint.value === "fixed") {
    parse.ranges = el.ranges.value
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => p.split("-").map((n) => Number(n)) as [number, number]);
    parse.header_lines = Number(el.headerLines.value || 0);
  }
  return {
    mode: el.mode.value as ImportIntent["mode"],
    scope: { kind: el.scopeKind.value as ImportIntent["scope"]["kind"], value: el.scopeValue.value.trim() },
    effective_time: new Date().toISOString(),
    field_update_policy: "absent_means_no_update",
    trust: el.trust.checked,
    owner_policy: el.ownerPolicy.value as ImportIntent["owner_policy"],
    format_hint: el.formatHint.value as ImportIntent["format_hint"],
    parse_options: parse,
  };
}

function refreshMapping(): void {
  el.mapping.hidden = el.formatHint.value !== "csv";
  el.fixed.hidden = el.formatHint.value !== "fixed";
  if (el.formatHint.value === "csv" && state.text !== null) {
    if (!state.mapping) state.mapping = proposeContract(state.text);
    renderMappingTable();
  }
}

function renderMappingTable(): void {
  const body = el.mappingTable.tBodies[0] as HTMLTableSectionElement;
  const m = state.mapping;
  if (!m) return;
  body.innerHTML = m.fields
    .map(
      (f, i) => html`<tr>
        <td class="id">${f.name}</td>
        <td class="id">${m.positional ? `position ${f.identity}` : `header "${f.identity}"`}</td>
        <td><select data-field="${i}" data-prop="type" class="select-in small"><option value="text" ${f.type === "text" ? "selected" : ""}>text</option><option value="identifier" ${f.type === "identifier" ? "selected" : ""}>equipment identifier</option></select></td>
        <td><input type="checkbox" data-field="${i}" data-prop="required" ${f.required ? "checked" : ""} aria-label="required ${f.name}"></td>
      </tr>`,
    )
    .join("");
  body.querySelectorAll<HTMLSelectElement | HTMLInputElement>("[data-field]").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset["field"]);
      const field = m.fields[idx];
      if (!field) return;
      if (input.dataset["prop"] === "type") field.type = (input as HTMLSelectElement).value as "text" | "identifier";
      else field.required = (input as HTMLInputElement).checked;
      m.version += 1;
    });
  });
}

el.formatHint.addEventListener("change", refreshMapping);

// ---- step 3: inspect --------------------------------------------------------

el.inspect.addEventListener("click", async () => {
  if (large.active()) {
    el.inspect.disabled = true;
    try {
      await large.inspect(readIntent(), state.mapping);
    } finally {
      el.inspect.disabled = false;
    }
    return;
  }
  if (!state.source) return;
  const intent = readIntent();
  const previous = state.analysis;
  if (previous && state.approvals.some((a) => a.state === "approved")) {
    const what = describeIntentChange(previous.intent, intent);
    state.approvals = state.approvals.map((a) => (a.state === "approved" ? { ...a, state: "stale", stale_reason: `re-analysis: ${what}` } : a));
    state.proposals = markStale(state.proposals, `re-analysis: ${what}`);
  }
  state.intent = intent;
  state.jobState = "inspecting";
  el.results.hidden = false;
  renderJobState();
  el.inspect.disabled = true;
  const bytes = state.source.bytes.slice();
  const res = (await ask(
    {
      type: "inspect",
      bytes: bytes.buffer as ArrayBuffer,
      filename: state.source.filename,
      content_type: state.source.content_type,
      intent,
      mapping: intent.format_hint === "csv" ? state.mapping : null,
      analysis_version: (previous?.version ?? 0) + 1,
    },
    [bytes.buffer as ArrayBuffer],
  )) as InspectResponse | { type: "error"; message: string };
  el.inspect.disabled = false;
  if (res.type === "error") {
    state.jobState = "failed";
    renderJobState(res.message);
    return;
  }
  state.analysis = res.analysis;
  state.proposals = res.analysis.proposals;
  state.selected = null;
  state.jobState = res.analysis.report ? "awaiting_review" : "failed";
  await refreshFingerprint();
  renderAll();
  el.exportSection.hidden = !res.analysis.report;
});

function describeIntentChange(a: ImportIntent, b: ImportIntent): string {
  const diffs: string[] = [];
  if (a.trust !== b.trust) diffs.push(`trust ${a.trust ? "on" : "off"} → ${b.trust ? "on" : "off"}`);
  if (a.owner_policy !== b.owner_policy) diffs.push(`owner policy ${a.owner_policy} → ${b.owner_policy}`);
  if (a.format_hint !== b.format_hint) diffs.push(`format ${a.format_hint} → ${b.format_hint}`);
  if (a.mode !== b.mode) diffs.push(`mode ${a.mode} → ${b.mode}`);
  if (JSON.stringify(a.parse_options) !== JSON.stringify(b.parse_options)) diffs.push("mapping changed");
  return diffs.length ? diffs.join(", ") : "same inputs re-run";
}

async function refreshFingerprint(): Promise<void> {
  if (!state.sourceFile || !state.analysis) return;
  state.fingerprint = await changeSetFingerprint({
    source_sha256: state.sourceFile.sha256,
    analysis_version: state.analysis.version,
    intent: state.intent,
    approved: state.proposals.filter((p) => p.state === "approved"),
  });
}

// ---- rendering ---------------------------------------------------------------

function renderJobState(error = ""): void {
  const labels: Record<string, string> = { uploaded: "Uploaded", inspecting: "Inspecting…", awaiting_review: "Awaiting review", failed: "Failed", exporting: "Exporting…", completed: "Completed" };
  const cls = state.jobState === "failed" ? "failed" : state.jobState === "inspecting" || state.jobState === "exporting" ? "pending" : state.jobState === "completed" ? "passed" : "neutral";
  const stale = state.approvals.filter((a) => a.state === "stale");
  el.jobState.innerHTML =
    html`<span class="badge ${cls}">${labels[state.jobState] ?? state.jobState}</span> <span class="muted small">analysis ${state.analysis?.version ?? "–"} · parser ${state.analysis?.parser_version ?? ""} · rules ${state.analysis?.ruleset_version ?? ""}</span>` +
    (error ? html`<p class="notice"><span class="notice-error">${error}</span></p>` : "") +
    (stale.length ? html`<p class="notice"><span class="notice-warn">${stale.length} earlier approval${stale.length === 1 ? "" : "s"} became stale: ${stale[stale.length - 1]?.stale_reason ?? ""}. Review the new proposals again.</span></p>` : "");
}

function renderTotals(): void {
  const a = state.analysis;
  if (!a) return;
  const s = a.report;
  const tiles: [string, string | number][] = [
    ["Format", a.detected_format],
    ["Physical lines", a.counts.physical_lines],
    ["Logical records", a.counts.logical_records ?? "n/a"],
    ["Identifier occurrences", a.counts.identifier_occurrences],
    ["Unique identifiers", a.counts.unique_identifiers],
    ["Corrections proposed", s ? s.corrected.length : 0],
    ["Flagged", s ? s.flagged.length : 0],
    ["Valid", s ? s.valid : 0],
    ["Invalid", s ? s.invalid : 0],
    ["Empty IDs", s ? s.empty_id : 0],
  ];
  el.totals.innerHTML = tiles.map(([k, v]) => html`<div class="tile"><span class="k">${k}</span><span class="v">${String(v)}</span></div>`).join("");
}

function renderFindings(): void {
  const a = state.analysis;
  if (!a) return;
  if (!a.findings.length) {
    el.findings.innerHTML = "";
    return;
  }
  el.findings.innerHTML =
    `<h3 class="sub-title">Findings</h3><ul class="findings">` +
    a.findings
      .map(
        (f) => html`<li><span class="badge ${f.severity === "blocking" ? "failed" : f.severity === "warning" ? "warning" : "neutral"}">${f.severity}</span> <b class="id">${f.code}</b> <span class="muted">blocks: ${f.blocking_scope}</span><br>${f.detail}${f.location ? raw(` <span class="muted">(${html`${f.location}`})</span>`) : ""}<br><span class="muted small">Next: ${f.recovery}</span></li>`,
      )
      .join("") +
    "</ul>";
}

function issueOf(p: Proposal): string {
  if (p.kind === "correction") return "check digit";
  if (/category letter/.test(p.reason)) return "unknown category";
  if (/owner code/.test(p.reason)) return "non-standard prefix";
  if (/Free-text/.test(p.reason)) return "free text";
  if (/N7-18/.test(p.reason)) return "missing X12 slot";
  if (/UIC/.test(p.reason)) return "UIC context";
  if (/deny list/.test(p.reason)) return "operator deny";
  return "other";
}

function filtered(): Proposal[] {
  return state.proposals.filter(
    (p) => (state.filter.kind === "all" || p.kind === state.filter.kind) && (state.filter.state === "all" || p.state === state.filter.state) && (state.filter.issue === "all" || issueOf(p) === state.filter.issue),
  );
}

function renderFacets(): void {
  const count = (fn: (p: Proposal) => string) => {
    const m = new Map<string, number>();
    for (const p of state.proposals) m.set(fn(p), (m.get(fn(p)) ?? 0) + 1);
    return m;
  };
  const group = (title: string, key: keyof State["filter"], counts: Map<string, number>) =>
    `<div class="facet"><span class="facet-title">${title}</span>` +
    [["all", state.proposals.length] as [string, number], ...counts.entries()]
      .map(([v, n]) => html`<button type="button" class="facet-item ${state.filter[key] === v ? "on" : ""}" data-key="${key}" data-value="${v}">${v} <span class="muted">${n}</span></button>`)
      .join("") +
    "</div>";
  el.facets.innerHTML = group("Kind", "kind", count((p) => p.kind)) + group("State", "state", count((p) => p.state)) + group("Issue", "issue", count(issueOf));
  el.facets.querySelectorAll<HTMLButtonElement>(".facet-item").forEach((b) =>
    b.addEventListener("click", () => {
      state.filter[b.dataset["key"] as keyof State["filter"]] = b.dataset["value"] as string;
      renderFacets();
      renderProposals();
    }),
  );
  const n = filtered().length;
  el.bulkScope.textContent = `Bulk actions apply to the ${n} filtered proposal${n === 1 ? "" : "s"} (all pages), not only the rows on screen.`;
}

function highlightChange(raw: string, candidate: string | null): string {
  if (!candidate) return "—";
  // Show the corrected number with the characters that differ from the original emphasised.
  let out = "";
  for (let i = 0; i < candidate.length; i++) {
    const ch = candidate[i] as string;
    out += raw[i] === ch ? html`${ch}` : html`<span class="cd">${ch}</span>`;
  }
  return out;
}

function renderProposals(): void {
  const rows = filtered();
  const body = el.proposals.tBodies[0] as HTMLTableSectionElement;
  el.proposalsEmpty.hidden = rows.length > 0;
  if (rows.length === 0) {
    el.proposalsEmptyText.textContent = state.proposals.length === 0
      ? "No changes to review. Every identifier passed, or none was found."
      : "No proposals match these filters. Change the filter to see the rest.";
  }
  body.innerHTML = rows
    .map(
      (p) => html`<tr data-id="${p.id}" class="${state.selected === p.id ? "selected" : ""}" tabindex="0">
        <td class="id">${p.id}</td>
        <td class="id">${p.raw}</td>
        <td class="id">${raw(highlightChange(p.raw, p.candidate))}</td>
        <td>${issueOf(p)}</td>
        <td class="small muted">${p.occurrences[0]?.label ?? ""}${p.occurrences.length > 1 ? ` +${p.occurrences.length - 1}` : ""}</td>
        <td>${raw(decisionBadge(p.state))}</td>
        <td class="actions">${
          p.kind === "correction"
            ? raw(html`<button type="button" class="btn btn-ghost btn-small" data-act="approve" data-id="${p.id}">Approve change</button>`)
            : ""
        }<button type="button" class="btn btn-ghost btn-small" data-act="reject" data-id="${p.id}">Keep original</button><button type="button" class="btn btn-ghost btn-small" data-act="defer" data-id="${p.id}">Review later</button>${
          p.state !== "proposed" ? raw(html`<button type="button" class="btn btn-ghost btn-small" data-act="undo" data-id="${p.id}">Undo</button>`) : ""
        }</td>
      </tr>`,
    )
    .join("");
  body.querySelectorAll<HTMLButtonElement>("[data-act]").forEach((b) =>
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      void applyDecision([b.dataset["id"] as string], b.dataset["act"] as "approve" | "reject" | "defer" | "undo");
    }),
  );
  body.querySelectorAll<HTMLTableRowElement>("tr[data-id]").forEach((tr) => {
    const select = () => {
      state.selected = tr.dataset["id"] as string;
      renderProposals();
      renderEvidence();
    };
    tr.addEventListener("click", (ev) => {
      // a click on a nested action button is that button's, not a row selection
      if ((ev.target as HTMLElement).closest("[data-act]")) return;
      select();
    });
    tr.addEventListener("keydown", (ev) => {
      // let nested buttons handle their own Enter/Space; only the row itself selects
      if (ev.target !== tr) return;
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        select();
      }
    });
  });
}

const DECISION_BADGE: Record<string, [string, string]> = {
  proposed: ["neutral", "Undecided"],
  approved: ["passed", "Approved"],
  rejected: ["neutral", "Kept original"],
  deferred: ["pending", "Review later"],
  stale: ["stale", "Stale — re-review"],
  applied_to_draft: ["passed", "Approved"],
};

function decisionBadge(state: string): string {
  const [cls, label] = DECISION_BADGE[state] ?? ["neutral", state];
  return html`<span class="badge ${cls}">${label}</span>`;
}

function renderEvidence(): void {
  const p = state.proposals.find((x) => x.id === state.selected);
  if (!p) {
    el.evidence.innerHTML = `<h3 class="sub-title">Evidence</h3><p class="muted small">Select a row to see its occurrences, rule and reason.</p>`;
    return;
  }
  const rec = state.analysis?.report?.containers.find((c) => c.normalized === p.normalized || c.as_found === p.raw);
  const near = rec?.near_misses ?? [];
  el.evidence.innerHTML =
    html`<h3 class="sub-title">Evidence for ${p.id}</h3>
    <dl class="kv">
      <dt>Raw</dt><dd class="id">${p.raw}</dd>
      <dt>Normalized</dt><dd class="id">${p.normalized}</dd>
      <dt>Candidate</dt><dd class="id">${p.candidate ?? "none"}</dd>
      <dt>Rule</dt><dd class="id">${p.rule_id} @ ${p.rule_version}</dd>
      <dt>Reason</dt><dd>${p.reason}</dd>
      <dt>Evidence</dt><dd>${p.evidence}</dd>
      <dt>Field context</dt><dd>${p.field_context}</dd>
      <dt>Confidence basis</dt><dd>${p.confidence_basis}</dd>
      <dt>Identity basis</dt><dd>${rec?.identity_basis ?? "unverified"}</dd>
      <dt>State</dt><dd>${p.state}${p.stale_reason ? ` (${p.stale_reason})` : ""}</dd>
    </dl>` +
    `<h4 class="sub-title">Occurrences (${p.occurrences.length}${p.occurrences.length > 1 ? ", kept in sync" : ""})</h4>` +
    `<ul class="occ">` +
    p.occurrences.map((o) => html`<li><span class="muted small">${o.label} · offset ${o.offset}</span><br><code class="before">${o.before}</code>${o.after !== o.before ? raw(`<br><code class="after">${html`${o.after}`}</code>`) : ""}</li>`).join("") +
    `</ul>` +
    (near.length
      ? `<h4 class="sub-title">Near misses in this file</h4><ul class="occ">` +
        near.map((n) => html`<li><span class="id">${n.eqid}</span> <span class="muted small">distance ${n.distance}, seen ${n.times_seen}× · proposal from this file's own check-valid bodies; not applied</span></li>`).join("") +
        `</ul>`
      : "") +
    html`<label class="check small"><span class="muted">Comment</span> <input type="text" class="text-in plain" id="comment" value="${p.comment}" maxlength="200"></label>`;
  document.getElementById("comment")?.addEventListener("change", (ev) => {
    const v = (ev.target as HTMLInputElement).value;
    state.proposals = state.proposals.map((x) => (x.id === p.id ? { ...x, comment: v } : x));
  });
}

async function applyDecision(ids: string[], act: "approve" | "reject" | "defer" | "undo"): Promise<void> {
  const active = document.activeElement as HTMLElement | null;
  const focusId = active?.dataset?.["id"];
  const focusAct = active?.dataset?.["act"];
  const { proposals, affected } = decide(state.proposals, ids, act);
  state.proposals = proposals;
  await refreshFingerprint();
  if (act === "approve" && affected > 0) {
    state.approvals.push({
      id: `ap${state.approvals.length + 1}`,
      proposal_ids: ids.filter((id) => proposals.find((p) => p.id === id)?.state === "approved"),
      change_set_fingerprint: state.fingerprint,
      approver: "local user",
      authorization_scope: "draft",
      decided_at: new Date().toISOString(),
      decision: "approve",
      state: "approved",
      stale_reason: null,
      selection_count: affected,
    });
  }
  // A decision changes the reviewed change set, so any built artefact is stale.
  invalidateExport();
  renderAll();
  if (focusId) restoreRowFocus(focusId, focusAct);
}

// Keep keyboard focus on the acted row after the table is rebuilt.
function restoreRowFocus(id: string, act: string | undefined): void {
  const row = el.proposals.querySelector<HTMLElement>(`tr[data-id="${CSS.escape(id)}"]`);
  if (!row) return;
  const target =
    (act ? row.querySelector<HTMLElement>(`[data-act="${CSS.escape(act)}"]`) : null) ??
    row.querySelector<HTMLElement>("[data-act]") ??
    row;
  target.focus();
}

for (const [button, act] of [
  [el.bulkApprove, "approve"],
  [el.bulkReject, "reject"],
  [el.bulkUndo, "undo"],
] as const) {
  button.addEventListener("click", () => {
    const rows = filtered();
    const eligible = act === "approve" ? rows.filter((p) => p.kind === "correction") : rows;
    if (!eligible.length) return;
    const verb = act === "approve" ? "Approve the change on" : act === "reject" ? "Keep the original for" : "Undo decisions on";
    if (confirm(`${verb} ${eligible.length} proposal${eligible.length === 1 ? "" : "s"} matching the current filter (of ${state.proposals.length} total)?`)) {
      void applyDecision(eligible.map((p) => p.id), act);
    }
  });
}

function renderExportState(): void {
  const undecided = state.proposals.filter((p) => p.state === "proposed").length;
  const stale = state.proposals.filter((p) => p.state === "stale").length;
  const approved = state.proposals.filter((p) => p.state === "approved").length;
  let msg = `${approved} approved · ${undecided} undecided · ${stale} stale · change set ${state.fingerprint.slice(0, 16)}…`;
  let blocked = false;
  if (stale > 0) {
    msg += " — blocked: stale approvals must be renewed or undone.";
    blocked = true;
  } else if (undecided > 0 && !state.allowUndecided) {
    msg += " — blocked: decide every proposal, or allow undecided items to be exported unchanged with an exception report.";
    blocked = true;
  }
  el.exportState.innerHTML = html`${msg}` + (undecided > 0 ? html` <label class="check small"><input type="checkbox" id="allow-undecided" ${state.allowUndecided ? "checked" : ""}> Export with undecided items unchanged (exception report attached)</label>` : "");
  document.getElementById("allow-undecided")?.addEventListener("change", (ev) => {
    state.allowUndecided = (ev.target as HTMLInputElement).checked;
    renderExportState();
  });
  el.build.disabled = blocked;
}

function renderAll(): void {
  renderJobState();
  renderTotals();
  renderFindings();
  renderFacets();
  renderProposals();
  renderEvidence();
  renderExportState();
}

// ---- step 4: export ---------------------------------------------------------

const urls: string[] = [];
function blobLink(a: HTMLAnchorElement, data: BlobPart, type: string, name: string): void {
  const url = URL.createObjectURL(new Blob([data], { type }));
  urls.push(url);
  a.href = url;
  a.download = name;
  a.hidden = false;
}

// A built artefact describes one exact reviewed change set. When the source,
// analysis, mapping or a review decision changes, the download no longer matches
// what is on screen, so it is withdrawn until the file is built again.
function invalidateExport(): void {
  for (const u of urls.splice(0)) URL.revokeObjectURL(u);
  for (const a of [el.dlCorrected, el.dlExceptions, el.dlLedger, el.dlManifest]) {
    a.hidden = true;
    a.removeAttribute("href");
  }
  el.dlExtras.hidden = true;
  el.manifestPreview.hidden = true;
  el.manifestPreview.textContent = "";
  if (state.jobState === "completed" || state.jobState === "exporting") state.jobState = "awaiting_review";
}

el.build.addEventListener("click", async () => {
  if (!state.text || !state.encoding || !state.sourceFile || !state.analysis) return;
  state.jobState = "exporting";
  renderJobState();
  el.build.disabled = true;
  el.dlExtras.hidden = true;
  for (const u of urls.splice(0)) URL.revokeObjectURL(u);
  const { edits } = applyApproved(state.text, state.proposals);
  const res = (await ask({ type: "apply", text: state.text, edits, encoding: state.encoding })) as ApplyResponse | { type: "error"; message: string };
  if (res.type === "error") {
    state.jobState = "failed";
    renderJobState(res.message);
    el.build.disabled = false;
    return;
  }
  // Decisions are not mutated by building: an approved proposal stays approved, so
  // rebuilding the same reviewed change set produces identical output.
  const stem = state.sourceFile.filename.replace(/(\.[^.]+)?$/, "");
  const ext = /\.[^.]+$/.exec(state.sourceFile.filename)?.[0] ?? ".txt";
  const outName = `${stem}.corrected${ext}`;
  const manifest: Manifest = {
    artifact_version: "1",
    source: state.sourceFile,
    analysis_version: state.analysis.version,
    parser_version: state.analysis.parser_version,
    ruleset_version: state.analysis.ruleset_version,
    intent: state.intent,
    detected_format: state.analysis.detected_format,
    output: { sha256: res.sha256, size_bytes: res.bytes.byteLength, encoding: state.encoding, output_mode: "surgical", filename: outName },
    change_set: { fingerprint: state.fingerprint, fingerprint_version: "1", approved: state.proposals.filter((p) => p.state === "approved").length, edits_applied: edits.length },
    approvals: state.approvals,
    counts: { ...state.analysis.counts, logical_records: state.analysis.counts.logical_records ?? -1 } as Record<string, number>,
    unresolved: state.proposals.filter((p) => p.state === "proposed" || p.state === "deferred").length,
    built_at: new Date().toISOString(),
    verification: {
      reparsed: res.reparsed !== null,
      remaining_corrections_in_output: res.reparsed?.corrected ?? -1,
      note: res.reparsed ? "output reparsed with trust on; remaining corrections are items that were rejected (kept original), deferred or undecided" : "output could not be reparsed",
    },
  };
  blobLink(el.dlCorrected, res.bytes, "application/octet-stream", outName);
  blobLink(el.dlExceptions, exceptionsCsv(state.proposals), "text/csv", `${stem}.exceptions.csv`);
  blobLink(el.dlLedger, ledgerCsv(state.proposals), "text/csv", `${stem}.ledger.csv`);
  const manifestText = JSON.stringify(manifest, null, 2);
  blobLink(el.dlManifest, manifestText, "application/json", `${stem}.manifest.json`);
  el.dlExtras.hidden = false;
  el.manifestPreview.hidden = false;
  el.manifestPreview.textContent = manifestText;
  state.jobState = "completed";
  renderAll();
  el.exportState.innerHTML = html`Artifact ready: <span class="id">${outName}</span> · ${res.bytes.byteLength.toLocaleString()} bytes · sha256 <span class="id">${res.sha256.slice(0, 16)}…</span> · ${edits.length} edit${edits.length === 1 ? "" : "s"} applied · output reparsed: ${res.reparsed ? `${res.reparsed.corrected} correction${res.reparsed.corrected === 1 ? "" : "s"} still possible (kept original, deferred or undecided), ${res.reparsed.valid} valid` : "no"}. A downloaded file is not the same as a successful import into a TOS.`;
});

refreshMapping();
