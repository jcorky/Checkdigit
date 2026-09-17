import { base, get, post, type Page } from "./api";
import { byId, esc, html, kv, mountWorkspaceSwitch, notice, num, param, raw, run, state, statusBadge, table, when } from "./ui";

interface Job {
  id: string; state: string; step: string; attempts: number; error: string | null; analysis_version: number;
  counts: Record<string, number>; counters: Record<string, number>; findings: Record<string, number>; blocking: string[];
  decisions: Record<string, number>; generation_id: string | null; import_intent_id: string | null; profile_id: string | null;
  profile_version: number | null; repair_of_feedback_id: string | null; dependency_pins: Record<string, string>; created_at: string;
}
interface Finding { id: string; code: string; severity: string; blocking_scope: string; explanation: string; recovery: string; location_label?: string }
interface Obs { ordinal: number; location: string; raw: string; token: string; normalized: string; scheme: string; status: string; decision: string; candidate: string | null; reason: string; version: number }
interface Approval { id: string; decision: string; state: string; approver: string; decided_at: string; selection_manifest: { count: number; digest: string }; filter: Record<string, unknown>; stale_reason: string | null }
interface Artifact { id: string; state: string; sha256?: string; built_at: string | null; counts: { edits_applied: number; exceptions: number | null; size_bytes: number | null }; error: string | null; lineage: { previous_artifact_id: string | null; repair_of_feedback_id: string | null } }
interface Message { id: string; message_no: number; message_ref: string; message_type: string; document_id: string; function: string; sender: string | null; receiver: string | null; lifecycle_resolution: string; resolution_detail: string; identifier_count: number; checks: { syntax: string; schema: string; partner: string; issues: Record<string, string[]> }; context: { vessel: string; voyage: string; pol: string; pod: string } }
interface Layer { layer: string; state: string; source: string; rule_version: string; detail: string }

const jobId = param("id") ?? "";
const msg = byId("msg");
let obsCursor: string | null = null;

function filter(): Record<string, string> {
  const f: Record<string, string> = {};
  const s = byId<HTMLSelectElement>("f-status").value;
  const d = byId<HTMLSelectElement>("f-decision").value;
  const p = byId<HTMLInputElement>("f-prefix").value.trim();
  if (s) f.status = s;
  if (d) f.decision = d;
  if (p) f.prefix = p;
  return f;
}

function query(f: Record<string, string>): string {
  return Object.entries(f).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
}

async function loadJob(): Promise<Job> {
  const j = await get<Job>(`/jobs/${jobId}`);
  byId("title").textContent = `Job ${j.id}`;
  const c = j.counters;
  byId("tiles").innerHTML = [
    ["State", raw(state(j.state))], ["Records", num(c.records_total)], ["Identifiers", num(c.identifiers_total)],
    ["Valid", num(c.status_valid ?? 0)], ["Corrected", num(c.status_corrected ?? 0)], ["Flagged", num(c.status_flagged ?? 0)],
    ["Invalid", num(c.status_invalid_structure ?? 0)], ["Missing", num(c.identifiers_missing ?? 0)],
  ].map(([l, n]) => html`<div class="tile-ws"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  byId("job-meta").innerHTML = kv([
    ["Step", `${esc(j.step || "—")} (attempt ${j.attempts})`],
    ["Analysis version", String(j.analysis_version)],
    ["Pins", esc(`${j.dependency_pins.parser_version}, ${j.dependency_pins.ruleset_version}, policy ${j.dependency_pins.policy_fingerprint || "none"}`)],
    ["Intent", j.import_intent_id ? html`<code>${j.import_intent_id}</code>` : "comparison only"],
    ["Generation", j.generation_id ? html`<code>${j.generation_id}</code>` : "—"],
    ["Profile", j.profile_id ? html`<code>${j.profile_id}</code> v${j.profile_version}` : "none"],
    ["Repair of feedback", j.repair_of_feedback_id ? html`<code>${j.repair_of_feedback_id}</code>` : "—"],
    ["Decisions", esc(Object.entries(j.decisions).map(([k, v]) => `${k} ${v}`).join(", ") || "none")],
    ["Blocking", j.blocking.length ? j.blocking.map((b) => html`<span class="state err">${b}</span>`).join(" ") : "none"],
    ["Error", esc(j.error ?? "—")], ["Created", when(j.created_at)],
  ]);
  return j;
}

async function loadFindings(): Promise<void> {
  const page = await get<Page<Finding>>(`/jobs/${jobId}/findings?limit=500`);
  byId("findings").innerHTML = table(["Code", "Severity", "Blocks", "Where", "Explanation", "Recovery"], page.items.map((f) => [
    html`<code>${f.code}</code>`, state(f.severity), esc(f.blocking_scope), esc(f.location_label ?? ""), esc(f.explanation), esc(f.recovery),
  ]), "No findings.");
}

async function loadObservations(reset = true): Promise<void> {
  if (reset) obsCursor = null;
  const f = filter();
  const page = await get<Page<Obs>>(`/jobs/${jobId}/observations?limit=100&${query(f)}${obsCursor ? `&cursor=${obsCursor}` : ""}`);
  const count = await get<{ count: number; proposals: number }>(`/jobs/${jobId}/observations/count?${query(f)}`);
  byId("match-count").textContent = `${num(count.count)} match, ${num(count.proposals)} undecided proposals`;
  const rows = page.items.map((o) => [
    String(o.ordinal), esc(o.location), html`<code>${o.token || o.raw}</code>`, statusBadge(o.status),
    o.candidate === null ? "—" : html`<code>${o.candidate}</code>`, state(o.decision), esc(o.reason),
    html`<button class="btn btn-ghost btn-small" data-layers="${o.ordinal}" type="button">Layers</button>`,
  ]);
  const container = byId("observations");
  const markup = table(["#", "Where", "Token", "Status", "Candidate", "Decision", "Reason", ""], rows, "No observations match.");
  container.innerHTML = reset ? markup : container.innerHTML + markup;
  obsCursor = page.next_cursor;
  byId<HTMLButtonElement>("more-obs").hidden = !obsCursor;
  container.querySelectorAll<HTMLButtonElement>("button[data-layers]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const out = await get<{ layers: Layer[]; context: Record<string, string | null> | null; normalized: string }>(`/jobs/${jobId}/observations/${b.dataset.layers}/layers`);
    byId("layers").innerHTML = html`<h3>Layers for ${out.normalized || "(empty)"}</h3>` + `<div class="layers">${out.layers
      .map((l) => `<div class="layer"><span>${esc(l.layer.replace(/_/g, " "))}</span>${state(l.state === "failed" ? "failed" : l.state)}<span>${esc(l.detail)} <span class="small">(${esc(l.source)}, ${esc(l.rule_version)})</span></span></div>`)
      .join("")}</div>` + (out.context ? kv(Object.entries(out.context).filter(([k]) => k !== "job_id").map(([k, v]) => [k.replace(/_/g, " "), esc(String(v ?? "—"))])) : "");
    byId("layers").scrollIntoView({ block: "nearest" });
  })));
}

async function decide(decision: string): Promise<void> {
  const f = filter();
  const count = await get<{ proposals: number }>(`/jobs/${jobId}/observations/count?${query(f)}`);
  const expected = "decision" in f ? undefined : count.proposals;
  if (!expected && !("decision" in f)) {
    notice(msg, "warn", "The filter matches no undecided proposals.");
    return;
  }
  const out = await post<{ id: string; selection_manifest: { count: number } }>(`/jobs/${jobId}/decisions`, {
    filter: f, decision, expected_count: expected, operation_id: `ui-${decision}-${Date.now()}`,
  });
  notice(msg, "ok", `${decision}: ${num(out.selection_manifest.count)} proposals frozen in approval ${out.id}.`);
  await Promise.all([loadObservations(), loadApprovals(), loadJob()]);
}

interface Connection { id: string; name: string; can_transmit: boolean }
let connections: Connection[] = [];

async function loadApprovals(): Promise<void> {
  const page = await get<Page<Approval & { authorization_scope: string }>>(`/jobs/${jobId}/approvals?limit=200`);
  byId("approvals").innerHTML = table(["Approval", "Decision", "State", "Count", "Filter", "By", "Scope", "When", ""], page.items.map((a) => [
    html`<code>${a.id}</code>`, state(a.decision === "approve" ? "approved" : a.decision === "reject" ? "rejected" : "deferred"),
    state(a.state) + (a.stale_reason ? html` <span class="small">${a.stale_reason}</span>` : ""), num(a.selection_manifest.count),
    esc(JSON.stringify(a.filter)), esc(a.approver), esc(a.authorization_scope), when(a.decided_at),
    a.decision === "approve" && a.state === "approved" && a.authorization_scope !== "transmission"
      ? html`<button class="btn btn-ghost btn-small" data-authorize="${a.id}" type="button">Authorize transmission</button>` : "",
  ]), "No decisions yet.");
  byId("approvals").querySelectorAll<HTMLButtonElement>("button[data-authorize]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const note = prompt("Customer's transmission authorization (who, ticket, date):") ?? "";
    await post(`/approvals/${b.dataset.authorize}/authorize-transmission`, { note });
    notice(msg, "ok", "Transmission authorized for this approval's edits.");
    await loadApprovals();
  })));
  try {
    connections = (await get<Page<Connection>>("/connections")).items.filter((c) => c.can_transmit);
  } catch {
    connections = [];
  }
  const sel = byId<HTMLSelectElement>("export-approval");
  sel.innerHTML = `<option value="">none (unchanged copy)</option>` + page.items
    .filter((a) => a.decision === "approve" && a.state === "approved")
    .map((a) => html`<option value="${a.id}">${a.id} (${a.selection_manifest.count})</option>`).join("");
  const arts = await get<Page<Artifact>>(`/jobs/${jobId}/artifacts?limit=100`);
  byId("artifacts").innerHTML = table(["Artifact", "State", "Edits", "Exceptions", "Bytes", "Built", "Files", "Delivery"], arts.items.map((a) => [
    html`<code>${a.id}</code>${a.lineage.repair_of_feedback_id ? raw(` <span class="state warn">repair</span>`) : ""}`, state(a.state) + (a.error ? html` <span class="small">${a.error}</span>` : ""),
    num(a.counts.edits_applied), num(a.counts.exceptions), num(a.counts.size_bytes), when(a.built_at),
    a.state === "ready" || a.state === "superseded"
      ? ["corrected", "exceptions", "ledger", "manifest"].map((n) => html`<a href="${base()}/artifacts/${a.id}/files/${n}">${n}</a>`).join(" · ")
      : "—",
    a.state === "ready"
      ? html`<button class="btn btn-ghost btn-small" data-deliver="${a.id}" type="button">Record delivery</button>` +
        (connections.length ? html` <select data-connection-for="${a.id}">${raw(connections.map((c) => html`<option value="${c.id}">${c.name}</option>`).join(""))}</select> <button class="btn btn-small" data-transmit="${a.id}" type="button">Transmit</button>` : "")
      : "",
  ]), "No artifacts yet.");
  byId("artifacts").querySelectorAll<HTMLButtonElement>("button[data-transmit]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const sel = byId("artifacts").querySelector<HTMLSelectElement>(`select[data-connection-for="${b.dataset.transmit}"]`);
    const out = await post<{ id: string; state: string; error: string | null; control_reference: string | null }>(`/artifacts/${b.dataset.transmit}/transmit`, {
      connection_id: sel?.value, idempotency_key: `ui-${Date.now()}`,
    });
    notice(msg, out.state === "delivery_confirmed" ? "ok" : "warn", `Transmission ${out.id}: ${out.state}${out.error ? ` (${out.error})` : ""}; control reference ${out.control_reference ?? "—"}.`);
  })));
  byId("artifacts").querySelectorAll<HTMLButtonElement>("button[data-deliver]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const destination = prompt("Destination (as delivered, for the record):");
    if (!destination) return;
    const st = prompt("Outcome: delivery_confirmed, failed or outcome_unknown", "delivery_confirmed") ?? "";
    const evidence = st === "delivery_confirmed" ? prompt("Evidence (transfer log line, receipt reference):") ?? "" : "";
    const ref = prompt("Interchange control reference sent (optional, correlates feedback):") ?? "";
    await post(`/artifacts/${b.dataset.deliver}/deliveries`, { destination, state: st, idempotency_key: `ui-${Date.now()}`, outcome_evidence: evidence || null, control_reference: ref || null });
    notice(msg, "ok", "Delivery recorded as manual evidence; it proves nothing about acceptance.");
  })));
}

async function loadMessages(): Promise<void> {
  const page = await get<Page<Message>>(`/jobs/${jobId}/messages?limit=200`);
  byId("messages").innerHTML = table(["#", "Type", "Ref", "Document", "Function", "Sender → receiver", "Identifiers", "Syntax", "Schema", "Partner", "Lifecycle", "Context"], page.items.map((m) => [
    String(m.message_no), esc(m.message_type), esc(m.message_ref), esc(m.document_id), esc(m.function), esc(`${m.sender ?? "?"} → ${m.receiver ?? "?"}`),
    num(m.identifier_count), state(m.checks.syntax === "failed" ? "failed" : m.checks.syntax), state(m.checks.schema === "failed" ? "failed" : m.checks.schema),
    state(m.checks.partner === "failed" ? "failed" : m.checks.partner),
    state(m.lifecycle_resolution) + (m.resolution_detail ? html` <span class="small">${m.resolution_detail}</span>` : "") +
      (Object.values(m.checks.issues).flat().length ? `<div class="small">${Object.values(m.checks.issues).flat().map(esc).join("; ")}</div>` : ""),
    esc([m.context.vessel, m.context.voyage, m.context.pol && `${m.context.pol}→${m.context.pod}`].filter(Boolean).join(" ")),
  ]), "No message envelope (delimited or text source).");
  const events = await get<Page<{ event_type: string; classifier: string; event_time: { raw: string; precision: string; tz_offset: string | null; parsed_utc: string | null; ambiguous: boolean }; message_no: number }>>(`/jobs/${jobId}/events?limit=200`);
  byId("events").innerHTML = events.items.length ? `<h3>Event assertions</h3>` + table(["Message", "Event", "Classifier", "Raw", "Precision", "Offset", "UTC"], events.items.map((e) => [
    String(e.message_no), esc(e.event_type), esc(e.classifier), html`<code>${e.event_time.raw}</code>`, esc(e.event_time.precision + (e.event_time.ambiguous ? " (ambiguous)" : "")),
    esc(e.event_time.tz_offset ?? "unknown"), esc(e.event_time.parsed_utc ?? "unresolved"),
  ])) : "";
}

async function loadInspection(): Promise<void> {
  const out = await get<{ kind: string; svg_by_bay?: Record<string, string>; rendered_bays?: number[]; omitted_bays?: number[]; units: { container_id: string; position?: { raw: string } | null; flags: string[]; observations: { status: string; decision: string; ordinal: number }[] }[]; vessel_name?: string }>(`/jobs/${jobId}/inspection`);
  const svgs = out.svg_by_bay ? Object.entries(out.svg_by_bay).map(([bay, svg]) => `<figure><figcaption>Bay ${esc(bay)}</figcaption>${svg}</figure>`).join("") : "";
  const units = out.units.map((u) => [
    html`<code>${u.container_id}</code>`, esc(u.position?.raw ?? "—"), esc(u.flags.join(", ")),
    u.observations.length ? u.observations.map((o) => statusBadge(o.status) + " " + state(o.decision)).join(" ") : html`<span class="state unavail">no observation</span>`,
  ]);
  byId("inspection").innerHTML = html`<p>${out.kind} · ${out.vessel_name ?? ""} · ${out.units.length} units${out.omitted_bays?.length ? ` · ${out.omitted_bays.length} bays not drawn` : ""}</p>`
    + `<div class="bay-grid">${svgs}</div>` + table(["Unit", "Position", "Flags", "Observation"], units);
}

function tabs(): void {
  document.querySelectorAll<HTMLButtonElement>(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll<HTMLButtonElement>(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll<HTMLElement>(".tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
    if (b.dataset.tab === "messages") run(msg, loadMessages);
    if (b.dataset.tab === "observations") run(msg, () => loadObservations());
    if (b.dataset.tab === "approvals") run(msg, loadApprovals);
  }));
}

mountWorkspaceSwitch();
tabs();
byId("refresh").addEventListener("click", () => run(msg, async () => { await loadJob(); await loadFindings(); }));
byId("run").addEventListener("click", () => run(msg, async () => { await post(`/jobs/${jobId}/run`); await loadJob(); await loadFindings(); }));
byId("cancel").addEventListener("click", () => run(msg, async () => { await post(`/jobs/${jobId}/cancel`); await loadJob(); }));
byId("reanalyze").addEventListener("click", () => run(msg, async () => {
  const out = await post<{ changed: number; approvals_stale: number; analysis_version: number }>(`/jobs/${jobId}/reanalyze`, { reason: "re-analysis from the workspace page" });
  notice(msg, "ok", `Analysis version ${out.analysis_version}: ${num(out.changed)} observations changed, ${out.approvals_stale} approvals stale.`);
  await loadJob(); await loadFindings();
}));
byId("apply-filter").addEventListener("click", () => run(msg, () => loadObservations()));
byId("more-obs").addEventListener("click", () => run(msg, () => loadObservations(false)));
byId("approve-all").addEventListener("click", () => run(msg, () => decide("approved")));
byId("reject-all").addEventListener("click", () => run(msg, () => decide("rejected")));
byId("defer-all").addEventListener("click", () => run(msg, () => decide("deferred")));
byId("export").addEventListener("click", () => run(msg, async () => {
  const approval = byId<HTMLSelectElement>("export-approval").value || null;
  const out = await post<{ id: string; state: string; counts: { edits_applied: number } }>(`/jobs/${jobId}/exports`, { approval_id: approval, idempotency_key: `ui-${approval ?? "none"}-${Date.now()}` });
  notice(msg, "ok", `Artifact ${out.id} ${out.state}: ${num(out.counts.edits_applied)} edits applied.`);
  await loadApprovals();
}));
byId("load-inspection").addEventListener("click", () => run(msg, loadInspection));
if (!jobId) {
  notice(msg, "err", "No job id in the address. Open a job from the jobs list.");
} else {
  run(msg, async () => { await loadJob(); await loadFindings(); });
}
