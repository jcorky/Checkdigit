# Open items

Anything not settled by the Python tree, the seed, the roadmap, or the Cloudflare
documentation fetched during the build is recorded here. Each item stays until it is
resolved with evidence, and the resolution is written next to it.

## Platform facts recorded (Cloudflare docs, fetched 2026-09-16)

`developers.cloudflare.com/workers/platform/limits/` (page dated 2026-09-05):

| Limit | Free plan |
|---|---|
| CPU time per HTTP request | 10 ms |
| Requests per day | 100,000 |
| Number of Workers | 100 |
| Subrequests per invocation | 50 |
| Request body size | 100 MB (Cloudflare plan limit, not Workers plan) |
| Worker size | 64 MiB |
| Static assets, files per Worker version | 20,000 |
| Static assets, individual file size | 25 MiB |

`developers.cloudflare.com/workers/static-assets/` and
`.../static-assets/billing-and-limitations/`:

- "Requests to static assets are free and unlimited."
- "Requests to the Worker script (for example, in the case of SSR content) are billed
  according to Workers pricing." With `run_worker_first`, matching requests always
  invoke the script and count against the free tier.
- No additional cost for storing assets.
- A `main` script is not required for an assets-only Worker; unmatched requests get a
  404 when no script is present.

`developers.cloudflare.com/workers/wrangler/configuration/` (`assets` keys):

- `directory`, `binding` (only useful with `main`), `html_handling` (default
  `auto-trailing-slash`; also `force-trailing-slash`, `drop-trailing-slash`, `none`),
  `not_found_handling` (default `none`; also `single-page-application`, `404-page`),
  `run_worker_first` (default `false`).
- Binding keys are top-level: `d1_databases`, `kv_namespaces`, `r2_buckets`,
  `durable_objects`, `queues`, `analytics_engine_datasets`. `tests/cost.test.ts`
  asserts none of them is present.

`developers.cloudflare.com/workers/configuration/routing/custom-domains/`:

- Dashboard path: Workers & Pages, select the Worker, Settings, Domains & Routes, Add,
  Custom Domain, enter the hostname, Add Custom Domain. Cloudflare creates the DNS
  records and issues the certificate.
- Hostnames match exactly: `thecheckdigit.com` and `www.thecheckdigit.com` are two
  separate Custom Domain entries.
- A Custom Domain cannot be created on a hostname that already has a CNAME record.

These numbers match the ones assumed at kickoff; nothing was overridden.

## Open

- OPEN: Custom Domain cost and per-Worker count are not stated on the custom-domains
  page. The kickoff assumption is "free on a zone already on Cloudflare". Confirm in
  the dashboard when binding the domain; stop if a charge is shown.
- OPEN: EN 13044 (ILU) check-digit arithmetic is unconfirmed against the normative
  text. Carried over from `checkdigit/equipment_checkdigit.py:32-33`. The port mirrors
  the kernel and keeps the caveat visible in the UI.
- OPEN: Lighthouse performance >= 95 can only be measured on the deployed URL. Not
  measurable before deploy.
- OPEN: Toolchain versions installed (TypeScript 7.0.2, Vite 8.3.0, Vitest 5.0.1,
  Wrangler 4.132.0) are newer than any documentation held offline. Their behaviour is
  verified only by running them in this repo (`npm run build`, `npm test`,
  `npx wrangler dev`). Any API used beyond what those runs exercise must be checked
  against the tool's own docs first.
- OPEN: npm did not run the post-install scripts of `esbuild` and `workerd` (npm
  `allow-scripts` policy on this machine). Vite build and `wrangler dev` were still
  exercised successfully with the platform-specific optional packages. If either
  breaks on a clean clone, run `npm approve-scripts esbuild workerd`.
- OPEN: The X12 golden pair has no input *file*. The input is the `SYNTH` string built
  in `checkdigit/run_pass6.py:33-40`; the expected output is
  `checkdigit/X12_synthetic.CORRECTED.edi`. The Phase 2 parity test must build the
  same string rather than read a sample file.

- OPEN: `@types/node` (7.x, type definitions only) was added as a dev dependency so
  `tsc --noEmit` can type-check `vite.config.ts`, `tests/` and `scripts/`. It ships no
  runtime code and never reaches the browser bundle. It is not on the allowed list by
  name; confirm or ask for it to be removed (removal means excluding those files from
  `tsc`).
- OPEN: Wrangler prints that it collects anonymous usage telemetry about the CLI
  itself. That concerns the developer tool, not the site. It can be switched off for
  this machine with `npx wrangler telemetry disable`; not done, as it is a user-level
  setting.

## Decisions taken during Pass 0 (stated assumptions)

- Real routes with a 404 page, not a single-page application. Each view is its own
  HTML entry (`/check` is served from `check.html`), so every route returns 200 as a
  static asset and no Worker script is needed. `not_found_handling` is `404-page`.
- Python used for the parity harness: `python3` is 3.13.14 on this machine (3.12 is
  also installed as `python`). Both run `test_equipment_checkdigit.py` 22/22 and
  `run_pass11.py` green. Nothing is blocked by Python version.
- `tests/vectors/calc_vectors.json` is regenerated with `npm run vectors`, which runs
  `checkdigit/run_pass11.py` and copies the file it reports. The vector file is
  committed.
