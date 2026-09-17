import { get, post, type Page } from "./api";
import { byId, esc, html, mountWorkspaceSwitch, notice, num, run, state, table, when } from "./ui";

interface Connection { id: string; name: string; kind: string; direction: string; partner: string; profile_id: string | null; state: string; duplicate_handling: string; authorized_by: string | null; authorization_note: string; verification: { ok?: boolean; error?: string; unavailable?: string }; can_transmit: boolean; can_poll: boolean; config: Record<string, unknown> }
interface Attempt { id: string; artifact_id: string; destination: string; state: string; control_reference: string | null; origin: string; error: string | null; resend_of_id: string | null; created_at: string; receipt: Record<string, unknown> | null }
interface InboxFile { id: string; filename: string; state: string; detail: string; job_id: string | null; feedback_id: string | null; seen_at: string }
interface Snapshot { id: string; source: string; version: string; retrieved_at: string; license_note: string; row_count: number }
interface Enrichment { id: string; provider: string; purpose: string; status: string; authorized_by: string; estimated_requests: number; budget: { max_requests?: number }; results: Record<string, unknown> }
interface Profile { id: string; name: string; version: number; verification_state: string }

const msg = byId("msg");

async function loadProfiles(): Promise<void> {
  const page = await get<Page<Profile>>("/profiles?limit=200");
  const sel = byId<HTMLSelectElement>("c-profile");
  sel.innerHTML = `<option value="">none</option>` + page.items.map((p) => html`<option value="${p.id}">${p.name} v${p.version} (${p.verification_state})</option>`).join("");
}

async function loadConnections(): Promise<void> {
  const page = await get<Page<Connection>>("/connections");
  byId("connections").innerHTML = table(["Connection", "Kind", "Direction", "Partner", "Profile", "Duplicates", "State", "Authorization", "Verification", ""], page.items.map((c) => [
    html`<strong>${c.name}</strong> <code>${c.id}</code>`, esc(c.kind), esc(c.direction), esc(c.partner || "—"),
    c.profile_id ? html`<code>${c.profile_id}</code>` : "—", esc(c.duplicate_handling.replace(/_/g, " ")), state(c.state),
    c.authorized_by ? html`${c.authorized_by}<div class="small">${c.authorization_note}</div>` : html`<span class="state warn">not authorized</span>`,
    c.verification.ok ? state("passed") : c.verification.error || c.verification.unavailable ? html`<span class="state err">${c.verification.error ?? c.verification.unavailable}</span>` : html`<span class="state unavail">not run</span>`,
    [
      html`<button class="btn btn-ghost btn-small" data-act="authorize" data-id="${c.id}" type="button">Authorize</button>`,
      html`<button class="btn btn-ghost btn-small" data-act="verify" data-id="${c.id}" type="button">Verify</button>`,
      c.state === "enabled" ? html`<button class="btn btn-ghost btn-small" data-act="disable" data-id="${c.id}" type="button">Disable</button>`
        : html`<button class="btn btn-small" data-act="enable" data-id="${c.id}" type="button">Enable</button>`,
      c.can_poll ? html`<button class="btn btn-ghost btn-small" data-act="poll" data-id="${c.id}" type="button">Poll inbox</button>` : "",
    ].join(" "),
  ]), "No connections. Files leave this server only through an enabled connection.");
  byId("connections").querySelectorAll<HTMLButtonElement>("button[data-act]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const id = b.dataset.id;
    switch (b.dataset.act) {
      case "authorize": {
        const note = prompt("Customer authorization on record (contract, ticket, date):") ?? "";
        await post(`/connections/${id}/authorize`, { note });
        break;
      }
      case "verify": {
        const out = await post<{ ok: boolean; error?: string; unavailable?: string }>(`/connections/${id}/verify`);
        notice(msg, out.ok ? "ok" : "warn", out.ok ? "Connectivity test passed." : `Connectivity test failed: ${out.error ?? out.unavailable}`);
        break;
      }
      case "enable": await post(`/connections/${id}/enable`); break;
      case "disable": await post(`/connections/${id}/disable`, { note: prompt("Reason:") ?? "" }); break;
      case "poll": {
        const out = await post<{ new: number; jobs: string[]; feedback: string[]; repairs: string[] }>(`/connections/${id}/poll`);
        notice(msg, "ok", `Inbox polled: ${out.new} new file(s), ${out.jobs.length} job(s), ${out.feedback.length} acknowledgment(s), ${out.repairs.length} repair draft(s).`);
        await Promise.all([loadInbox(), loadTransmissions()]);
        break;
      }
    }
    await loadConnections();
  })));
}

async function createConnection(): Promise<void> {
  const out = await post<Connection>("/connections", {
    name: byId<HTMLInputElement>("c-name").value.trim(), kind: byId<HTMLSelectElement>("c-kind").value,
    direction: byId<HTMLSelectElement>("c-direction").value, partner: byId<HTMLInputElement>("c-partner").value.trim(),
    profile_id: byId<HTMLSelectElement>("c-profile").value || null, duplicate_handling: byId<HTMLSelectElement>("c-dup").value,
    config: JSON.parse(byId<HTMLTextAreaElement>("c-config").value || "{}"),
  });
  notice(msg, "ok", `Connection ${out.name} created as ${out.state}. Authorize, verify, then enable it.`);
  await loadConnections();
}

async function loadTransmissions(): Promise<void> {
  const page = await get<Page<Attempt>>("/transmissions?limit=200");
  byId("transmissions").innerHTML = table(["Attempt", "Artifact", "Destination", "State", "Control ref", "Origin", "Detail", "When", ""], page.items.map((a) => [
    html`<code>${a.id}</code>${a.resend_of_id ? raw_(` <span class="small">resend of ${esc(a.resend_of_id)}</span>`) : ""}`,
    html`<code>${a.artifact_id}</code>`, esc(a.destination), state(a.state), esc(a.control_reference ?? "—"), esc(a.origin),
    esc(a.error ?? (a.receipt ? JSON.stringify(a.receipt).slice(0, 120) : "")), when(a.created_at),
    a.state === "failed" || a.state === "outcome_unknown" ? html`<button class="btn btn-ghost btn-small" data-resend="${a.id}" type="button">Resend</button>` : "",
  ]), "No transmissions yet.");
  byId("transmissions").querySelectorAll<HTMLButtonElement>("button[data-resend]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const note = prompt("Administrator note (required after an unknown outcome unless the receiver rejects duplicates):") ?? "";
    const out = await post<Attempt>(`/transmissions/${b.dataset.resend}/resend`, { idempotency_key: `ui-${Date.now()}`, note: note || null });
    notice(msg, out.state === "delivery_confirmed" ? "ok" : "warn", `Resend ${out.id}: ${out.state}${out.error ? ` (${out.error})` : ""}.`);
    await loadTransmissions();
  })));
}

function raw_(markup: string): { markup: string } {
  return { markup } as unknown as { markup: string };
}

async function loadInbox(): Promise<void> {
  const page = await get<Page<InboxFile>>("/inbox?limit=200");
  byId("inbox").innerHTML = table(["File", "State", "Detail", "Job", "Feedback", "Seen"], page.items.map((f) => [
    esc(f.filename), state(f.state === "job_created" || f.state === "feedback_recorded" ? "ok" : f.state === "failed" ? "failed" : f.state),
    esc(f.detail), f.job_id ? html`<a href="/workspace/job?id=${f.job_id}">job</a>` : "—", f.feedback_id ? html`<code>${f.feedback_id}</code>` : "—", when(f.seen_at),
  ]), "Nothing has arrived through an inbox connection yet.");
}

async function loadReference(): Promise<void> {
  const ref = await get<{ items: Snapshot[]; owner_codes: number }>("/reference");
  byId("reference").innerHTML = html`<p class="small">Owner codes held: ${num(ref.owner_codes)}</p>` + table(["Snapshot", "Source", "Version", "Rows", "Licence", "Retrieved"], ref.items.map((s) => [
    html`<code>${s.id}</code>`, esc(s.source), esc(s.version), num(s.row_count), esc(s.license_note), when(s.retrieved_at),
  ]), "No reference snapshots; the prefix registration layer stays not checked.");
  const enr = await get<{ items: Enrichment[]; providers_configured: string[] }>("/enrichment");
  byId("enrichment").innerHTML = html`<p class="small">Providers configured on this server: ${enr.providers_configured.join(", ") || "none (no credentials in the environment)"}</p>` + table(["Request", "Provider", "Purpose", "Status", "Authorized by", "Estimate / budget", "Results", ""], enr.items.map((e) => [
    html`<code>${e.id}</code>`, esc(e.provider), esc(e.purpose), state(e.status === "completed" ? "ok" : e.status === "failed" ? "failed" : e.status), esc(e.authorized_by || "—"),
    `${num(e.estimated_requests)} / ${num(e.budget.max_requests ?? null)}`, esc(JSON.stringify(e.results).slice(0, 160)),
    [html`<button class="btn btn-ghost btn-small" data-eact="authorize" data-id="${e.id}" type="button">Authorize</button>`,
     html`<button class="btn btn-small" data-eact="run" data-id="${e.id}" type="button">Run</button>`].join(" "),
  ]), "No enrichment requests.");
  byId("enrichment").querySelectorAll<HTMLButtonElement>("button[data-eact]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    if (b.dataset.eact === "authorize") {
      await post(`/enrichment/${b.dataset.id}/authorize`, { note: prompt("Who approved the provider terms and the spend:") ?? "" });
    } else {
      const out = await post<Enrichment>(`/enrichment/${b.dataset.id}/run`);
      notice(msg, out.status === "completed" ? "ok" : "warn", `Request ${out.id} is ${out.status}: ${JSON.stringify(out.results).slice(0, 200)}`);
    }
    await loadReference();
  })));
}

mountWorkspaceSwitch();
byId("c-create").addEventListener("click", () => run(msg, createConnection));
byId("tick").addEventListener("click", () => run(msg, async () => {
  const out = await post<{ recovered_deliveries: string[]; polls: { new?: number; error?: string }[] }>("/automation/tick");
  notice(msg, "ok", `Tick: ${out.recovered_deliveries.length} stale send(s) recovered, ${out.polls.length} inbox(es) polled (${out.polls.map((p) => p.error ?? `${p.new ?? 0} new`).join(", ") || "none enabled"}).`);
  await Promise.all([loadInbox(), loadTransmissions()]);
}));
byId("r-import").addEventListener("click", () => run(msg, async () => {
  await post("/reference/owner-register", { text: byId<HTMLTextAreaElement>("r-text").value, version: byId<HTMLInputElement>("r-version").value, license_note: byId<HTMLInputElement>("r-license").value });
  notice(msg, "ok", "Register imported; the prefix registration layer now reports against it.");
  await loadReference();
}));
byId("e-create").addEventListener("click", () => run(msg, async () => {
  const fields = byId<HTMLInputElement>("e-fields").value.split(",").map((s) => s.trim()).filter(Boolean);
  await post("/enrichment", { provider: byId<HTMLInputElement>("e-provider").value.trim(), purpose: byId<HTMLInputElement>("e-purpose").value.trim(),
    scope: { job_id: byId<HTMLInputElement>("e-job").value.trim() }, fields, estimated_requests: Number(byId<HTMLInputElement>("e-estimate").value || 0),
    budget: { max_requests: Number(byId<HTMLInputElement>("e-max").value || 0) } });
  await loadReference();
}));
run(msg, async () => { await Promise.all([loadProfiles(), loadConnections(), loadTransmissions(), loadInbox(), loadReference()]); });
