import { get, post, uploadFile, workspaceId, type Page } from "./api";
import { byId, html, kv, mountWorkspaceSwitch, notice, num, raw, run, state, table, when } from "./ui";

interface Job {
  id: string; state: string; created_at: string; import_intent_id: string | null; generation_id: string | null;
  counts: { logical_records?: number; identifier_occurrences?: number }; blocking: string[]; findings: Record<string, number>;
  profile_id: string | null; profile_version: number | null;
}
interface Generation { id: string; state: string; scope: { kind: string; value: string }; member_count: number; effects: Record<string, unknown>; published_at: string | null; job_id: string | null }
interface Profile { id: string; name: string; version: number; message_family: string; verification_state: string }

const msg = byId("msg");
let jobsCursor: string | null = null;

async function loadSummary(): Promise<void> {
  const ws = await get<{ name: string; current_generation_id: string | null; jobs: Record<string, number>; fleet_count: number; retention_policy: { source_retention_days: number } }>("");
  const total = Object.values(ws.jobs).reduce((a, b) => a + b, 0);
  byId("summary").innerHTML = [
    ["Workspace", workspaceId()], ["Jobs", num(total)], ["Awaiting review", num(ws.jobs.awaiting_review ?? 0)],
    ["Fleet members", num(ws.fleet_count)], ["Retention", `${ws.retention_policy.source_retention_days} days`],
  ].map(([l, n]) => html`<div class="tile-ws"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  byId("fleet").innerHTML = html`Current generation: <code>${ws.current_generation_id ?? "none"}</code> · fleet members: ${num(ws.fleet_count)}`;
}

async function loadProfiles(): Promise<void> {
  const page = await get<Page<Profile>>("/profiles?limit=200");
  const sel = byId<HTMLSelectElement>("profile");
  for (const p of page.items) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.name} v${p.version} (${p.message_family || "delimited"}, ${p.verification_state})`;
    sel.appendChild(o);
  }
}

async function loadJobs(reset = true): Promise<void> {
  if (reset) jobsCursor = null;
  const page = await get<Page<Job>>(`/jobs?limit=50${jobsCursor ? `&cursor=${encodeURIComponent(jobsCursor)}` : ""}`);
  const rows = page.items.map((j) => [
    html`<a href="/workspace/job?id=${j.id}"><code>${j.id}</code></a>`,
    state(j.state),
    num(j.counts.logical_records), num(j.counts.identifier_occurrences),
    j.blocking.length ? j.blocking.map((b) => html`<span class="state err">${b}</span>`).join(" ") : Object.keys(j.findings).length ? html`<span class="state warn">${Object.keys(j.findings).length} finding kinds</span>` : "",
    j.profile_id ? html`<code>${j.profile_id}</code> v${j.profile_version}` : "—",
    when(j.created_at),
  ]);
  const container = byId("jobs");
  const markup = table(["Job", "State", "Records", "Identifiers", "Findings", "Profile", "Created"], rows, "No jobs yet.");
  container.innerHTML = reset ? markup : container.innerHTML + markup;
  jobsCursor = page.next_cursor;
  byId<HTMLButtonElement>("more-jobs").hidden = !jobsCursor;
}

async function loadGenerations(): Promise<void> {
  const page = await get<Page<Generation>>("/generations?limit=100");
  const rows = page.items.map((g) => {
    const fx = g.effects as { added?: number; changed?: number; retire?: number; blocked?: string[] };
    const actions = g.state === "publishable"
      ? html`<button class="btn btn-small" data-publish="${g.id}" type="button">Publish</button> <button class="btn btn-ghost btn-small" data-abandon="${g.id}" type="button">Abandon</button>`
      : g.state === "candidate" ? html`<button class="btn btn-ghost btn-small" data-abandon="${g.id}" type="button">Abandon</button>` : "";
    return [
      html`<code>${g.id}</code>`, state(g.state), html`${g.scope.kind}/${g.scope.value}`, num(g.member_count),
      fx.added === undefined ? "—" : html`+${num(fx.added)} / ~${num(fx.changed)} / −${num(fx.retire)}${fx.blocked?.length ? raw(` <span class="state err">${fx.blocked.join(", ")}</span>`) : ""}`,
      g.job_id ? html`<a href="/workspace/job?id=${g.job_id}">job</a>` : "—", when(g.published_at), actions,
    ];
  });
  byId("generations").innerHTML = table(["Generation", "State", "Scope", "Members", "Added / changed / retire", "Job", "Published", ""], rows, "No generations yet.");
  byId("generations").querySelectorAll<HTMLButtonElement>("button[data-publish]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    const out = await post<{ state: string; fleet_count: number; replayed: boolean }>(`/generations/${b.dataset.publish}/publish`, { operation_id: `ui-${b.dataset.publish}` });
    notice(msg, "ok", `Generation ${out.state}; fleet now ${num(out.fleet_count)} members.`);
    await Promise.all([loadGenerations(), loadSummary()]);
  })));
  byId("generations").querySelectorAll<HTMLButtonElement>("button[data-abandon]").forEach((b) => b.addEventListener("click", () => run(msg, async () => {
    await post(`/generations/${b.dataset.abandon}/abandon`);
    await loadGenerations();
  })));
}

async function loadUsage(): Promise<void> {
  const u = await get<{ bytes: Record<string, number>; budget_bytes: number | null; min_free_bytes: number; rows: Record<string, number> }>("/usage");
  byId("usage").innerHTML = kv([
    ["Sources", `${num(u.bytes.sources)} bytes`], ["Uploads in progress", `${num(u.bytes.uploads)} bytes`],
    ["Artifacts", `${num(u.bytes.artifacts)} bytes`], ["Budget", u.budget_bytes ? `${num(u.budget_bytes)} bytes` : "unlimited"],
    ["Free disk required", `${num(u.min_free_bytes)} bytes`], ["Rows", Object.entries(u.rows).map(([k, v]) => `${k} ${num(v)}`).join(", ")],
  ]);
}

async function submit(): Promise<void> {
  const fileInput = byId<HTMLInputElement>("file");
  const file = fileInput.files?.[0];
  if (!file) {
    notice(msg, "warn", "Choose a file first.");
    return;
  }
  const status = byId("submit-status");
  const bar = byId("progress");
  status.textContent = "uploading…";
  const sid = await uploadFile(file, (sent, total) => {
    bar.style.width = `${Math.round((sent / total) * 100)}%`;
  });
  status.textContent = "registering intent…";
  const mode = byId<HTMLSelectElement>("mode").value;
  const ws = await get<{ current_generation_id: string | null }>("");
  let intentId: string | null = null;
  if (mode !== "comparison_only") {
    const declared = byId<HTMLInputElement>("declared").value;
    const intent = await post<{ import_intent_id: string; duplicate: boolean }>("/intents", {
      source_file_id: sid, mode, scope_kind: byId<HTMLSelectElement>("scope-kind").value,
      scope_value: byId<HTMLInputElement>("scope-value").value || "all",
      baseline_generation_id: ws.current_generation_id, declared_record_count: declared ? Number(declared) : null,
    });
    intentId = intent.import_intent_id;
  }
  const columns = byId<HTMLInputElement>("columns").value.split(",").map((s) => s.trim()).filter(Boolean);
  const attrs = byId<HTMLInputElement>("attr-columns").value.split(",").map((s) => s.trim()).filter(Boolean);
  const options: Record<string, unknown> = { owner_policy: byId<HTMLSelectElement>("owner-policy").value };
  if (columns.length) options.columns = columns;
  if (attrs.length) options.attribute_columns = attrs;
  const profile = byId<HTMLSelectElement>("profile").value || null;
  const job = await post<{ job_id: string; duplicate: boolean }>("/jobs", { source_file_id: sid, import_intent_id: intentId, profile_id: profile, options });
  if (job.duplicate) {
    notice(msg, "warn", `This intent already has a job: ${job.job_id}.`);
  }
  if (byId<HTMLInputElement>("run-inline").checked) {
    status.textContent = "analysing…";
    await post(`/jobs/${job.job_id}/run`);
  }
  location.href = `/workspace/job?id=${encodeURIComponent(job.job_id)}`;
}

mountWorkspaceSwitch();
byId("submit").addEventListener("click", () => run(msg, submit));
byId("more-jobs").addEventListener("click", () => run(msg, () => loadJobs(false)));
byId("purge").addEventListener("click", () => run(msg, async () => {
  const out = await post<{ jobs: number; sources: number; artifact_files: number; bytes_freed: number }>("/maintenance/purge");
  byId("purge-status").textContent = `purged ${out.jobs} jobs, ${out.sources} sources, ${out.artifact_files} artifact sets; ${num(out.bytes_freed)} bytes freed`;
  await Promise.all([loadUsage(), loadJobs(), loadSummary()]);
}));
run(msg, async () => {
  await Promise.all([loadSummary(), loadProfiles(), loadJobs(), loadGenerations(), loadUsage()]);
});
