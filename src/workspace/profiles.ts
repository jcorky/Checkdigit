import { del, get, post, put, type Page } from "./api";
import { byId, esc, html, mountWorkspaceSwitch, notice, num, run, state, table, when } from "./ui";

interface Profile { id: string; name: string; version: number; message_family: string; terminal_site: string; partner: string; verification_state: string; verification_note: string; acceptance_fixtures: string[]; rules: Record<string, unknown>; previous_version_id?: string }
interface Feedback { id: string; origin: string; response_kind: string; received_at: string; correlation: { state: string; message_refs: string[]; attempt_id: string | null }; technical_ack_state: string; business_processing_state: string; items: { export_item_ref: string; outcome: string; code: string; known: boolean; text: string }[]; repair_job_id: string | null }
interface Visit { id: string; terminal_site: string; composite_key: string | null; vessel: string | null; voyage: string | null; unresolved_association: string | null; observation_count: number; movements: { mode: string; origin: string | null; destination: string | null }[] }

const msg = byId("msg");
const DEFAULT_RULES = {
  required_segments: ["BGM", "TDT"], expected_sender: null, expected_receiver: null, expected_message_version: null,
  sequence: "message_ref_numeric", business_key: ["sender", "receiver", "message_type", "document_id"],
  revision_key: ["sender", "receiver", "message_type", "document_id", "message_ref"], original_on_existing: "conflict",
};

async function loadProfiles(): Promise<void> {
  const page = await get<Page<Profile>>("/profiles?limit=200");
  byId("profiles").innerHTML = table(["Profile", "Version", "Family", "Terminal", "Partner", "Verification", "Fixtures", ""], page.items.map((p) => [
    html`<strong>${p.name}</strong> <code>${p.id}</code>`, String(p.version), esc(p.message_family), esc(p.terminal_site), esc(p.partner),
    state(p.verification_state) + (p.verification_note ? html` <span class="small">${p.verification_note}</span>` : ""), num(p.acceptance_fixtures.length),
    html`<button class="btn btn-ghost btn-small" data-fixtures="${p.id}" type="button">Run fixtures</button> <button class="btn btn-ghost btn-small" data-verify="${p.id}" type="button">Record verification</button>`,
  ]), "No profiles yet. Every message feed should run under a versioned profile.");
  const sel = byId<HTMLSelectElement>("fb-profile");
  sel.innerHTML = `<option value="">none</option>` + page.items.map((p) => html`<option value="${p.id}">${p.name} v${p.version}</option>`).join("");
  byId("profiles").querySelectorAll<HTMLButtonElement>("button[data-fixtures]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const out = await post<{ ok: boolean; state: string; results: { filename?: string; ok: boolean; findings: { code: string; detail: string }[] }[] }>(`/profiles/${b.dataset.fixtures}/verify-fixtures`);
    notice(msg, out.ok ? "ok" : "warn", `Fixtures ${out.ok ? "clean" : "not clean"}; profile is ${out.state}. ` + out.results.map((r) => `${r.filename ?? "?"}: ${r.ok ? "ok" : r.findings.map((f) => f.code).join(", ")}`).join(" · "));
    await loadProfiles();
  })));
  byId("profiles").querySelectorAll<HTMLButtonElement>("button[data-verify]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const st = prompt("Verification state: partner_tested, production_enabled or unverified", "partner_tested");
    if (st === null) return;
    const note = prompt("Evidence note (partner test, ticket, date):");
    if (note === null) return;
    await post(`/profiles/${b.dataset.verify}/verification`, { state: st, note });
    await loadProfiles();
  })));
}

async function createProfile(): Promise<void> {
  const rulesText = byId<HTMLTextAreaElement>("p-rules").value.trim();
  const contractText = byId<HTMLTextAreaElement>("p-contract").value.trim();
  const fixtures = byId<HTMLInputElement>("p-fixtures").value.split(",").map((s) => s.trim()).filter(Boolean);
  const body = {
    name: byId<HTMLInputElement>("p-name").value.trim(), message_family: byId<HTMLSelectElement>("p-family").value,
    terminal_site: byId<HTMLInputElement>("p-terminal").value.trim(), partner: byId<HTMLInputElement>("p-partner").value.trim(),
    system: byId<HTMLInputElement>("p-system").value.trim(), system_version: byId<HTMLInputElement>("p-system-version").value.trim(),
    message_version: byId<HTMLInputElement>("p-message-version").value.trim(),
    rules: rulesText ? JSON.parse(rulesText) : null, mapping_contract: contractText ? JSON.parse(contractText) : null,
    acceptance_fixtures: fixtures.length ? fixtures : null,
  };
  const out = await post<Profile>("/profiles", body);
  notice(msg, "ok", `Profile ${out.name} version ${out.version} created (unverified).`);
  await loadProfiles();
}

async function loadFeedback(): Promise<void> {
  const page = await get<Page<Feedback>>("/feedback?limit=200");
  byId("feedback").innerHTML = table(["Feedback", "Origin", "Kind", "Correlation", "Technical", "Business", "Items", "Repair", "Received"], page.items.map((f) => [
    html`<code>${f.id}</code>`, esc(f.origin), esc(f.response_kind), state(f.correlation.state) + html` <span class="small">${f.correlation.message_refs.join(", ")}</span>`,
    state(f.technical_ack_state), state(f.business_processing_state),
    f.items.map((i) => html`<span class="state ${i.known ? (i.outcome === "rejected" ? "err" : i.outcome === "accepted" ? "ok" : "unavail") : "warn"}" title="${i.text}">${i.export_item_ref}: ${i.code || i.outcome}${i.known ? "" : " (unknown)"}</span>`).join(" "),
    f.repair_job_id ? html`<a href="/workspace/job?id=${f.repair_job_id}">repair job</a>` : (f.technical_ack_state === "rejected" || f.business_processing_state === "rejected" || f.business_processing_state === "partially_accepted") && f.correlation.state === "exact"
      ? html`<button class="btn btn-small" data-repair="${f.id}" type="button">Start repair</button>` : "—",
    when(f.received_at),
  ]), "No feedback recorded. Uploaded acknowledgments and manual outcomes are labelled as such; silence is never acceptance.");
  byId("feedback").querySelectorAll<HTMLButtonElement>("button[data-repair]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const out = await post<{ job_id: string }>(`/feedback/${b.dataset.repair}/repair`);
    notice(msg, "ok", `Repair job ${out.job_id} created from the delivered artifact.`);
    await loadFeedback();
  })));
}

async function recordFeedback(): Promise<void> {
  const origin = byId<HTMLSelectElement>("fb-origin").value;
  const text = byId<HTMLTextAreaElement>("fb-text").value;
  const body: Record<string, unknown> = { origin, response_profile_id: byId<HTMLSelectElement>("fb-profile").value || null, decision_by: byId<HTMLInputElement>("fb-decided").value || null };
  if (origin === "manual_outcome") body.manual = JSON.parse(text);
  else body.text = text;
  const out = await post<Feedback & { finding?: string }>("/feedback", body);
  notice(msg, out.correlation.state === "exact" ? "ok" : "warn", `Feedback ${out.id}: correlation ${out.correlation.state}, technical ${out.technical_ack_state}, business ${out.business_processing_state}${out.finding ? ` (${out.finding})` : ""}.`);
  await loadFeedback();
}

async function loadVisits(): Promise<void> {
  const page = await get<Page<Visit>>("/visits?limit=200");
  byId("visits").innerHTML = table(["Visit", "Terminal", "Key", "Vessel / voyage", "Movements", "Observations", "Unresolved"], page.items.map((v) => [
    html`<code>${v.id}</code>`, esc(v.terminal_site || "—"), esc(v.composite_key ?? "—"), esc(`${v.vessel ?? "?"} / ${v.voyage ?? "?"}`),
    esc(v.movements.map((m) => `${m.mode} ${m.origin ?? "?"}→${m.destination ?? "?"}`).join("; ")), num(v.observation_count),
    v.unresolved_association ? html`<span class="state warn">${v.unresolved_association}</span>` : state("passed"),
  ]), "No visits yet; message feeds create them.");
}

async function loadRoles(): Promise<void> {
  const page = await get<Page<{ user_id: string; role: string }>>("/roles");
  byId("roles").innerHTML = table(["User", "Role", ""], page.items.map((r) => [
    esc(r.user_id), state(r.role === "administrator" ? "ok" : r.role), html`<button class="btn btn-ghost btn-small" data-remove="${r.user_id}" type="button">Remove</button>`,
  ]), "No role assignments: every admin-session user administers this workspace.");
  byId("roles").querySelectorAll<HTMLButtonElement>("button[data-remove]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    await del(`/roles/${encodeURIComponent(b.dataset.remove ?? "")}`);
    await loadRoles();
  })));
}

mountWorkspaceSwitch();
byId<HTMLTextAreaElement>("p-rules").value = JSON.stringify(DEFAULT_RULES, null, 2);
byId("p-create").addEventListener("click", () => run(msg, createProfile));
byId("fb-record").addEventListener("click", () => run(msg, recordFeedback));
byId("r-set").addEventListener("click", () => run(msg, async () => {
  await put(`/roles/${encodeURIComponent(byId<HTMLInputElement>("r-user").value.trim())}`, { role: byId<HTMLSelectElement>("r-role").value });
  await loadRoles();
}));
run(msg, async () => { await Promise.all([loadProfiles(), loadFeedback(), loadVisits(), loadRoles()]); });
