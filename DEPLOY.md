# Deploying

The site is a Cloudflare Workers project that serves static assets only. There is no
Worker script, no binding, and no server-side state. Static asset requests are free
and unmetered on the Workers Free plan.

## Build and check

```bash
npm ci
npm test
npm run build
npx wrangler dev
```

`wrangler dev` serves `dist/` locally; every route should answer 200 and an unknown
path should answer 404 with the site's own page.

## First deploy

```bash
npx wrangler login
npx wrangler deploy
```

`wrangler deploy` uploads `dist/` and creates the Worker named in `wrangler.jsonc`.
It prints the `*.workers.dev` URL. Deploy only after the build and tests are green
and the deploy has been explicitly approved.

## Custom domain

The zone `thecheckdigit.com` must already be on Cloudflare. Do not edit DNS by hand;
Cloudflare creates the records when the Custom Domain is added.

1. In the Cloudflare dashboard open **Workers & Pages** and select the Worker
   `thecheckdigit`.
2. Go to **Settings**, then **Domains & Routes**, then **Add**, then **Custom Domain**.
3. Enter `thecheckdigit.com` and select **Add Custom Domain**.
4. Repeat steps 2 and 3 for `www.thecheckdigit.com`. Custom Domains match hostnames
   exactly, so the apex and `www` are separate entries.
5. Wait for the certificate to be issued (the dashboard shows the status), then open
   both hostnames over HTTPS.

A Custom Domain cannot be added to a hostname that already has a CNAME record. If
either hostname has one, remove that record first and note it here.

### How it was done (2026-09-17)

The Custom Domains are declared as `routes` with `custom_domain: true` in
`wrangler.jsonc`, so `wrangler deploy` creates them without the dashboard steps above.
Before that deploy the owner deleted the two records the zone still carried from the
earlier self-hosted setup: the apex CNAME to a Cloudflare Tunnel and an A record for
`www`. The first deploy without routes served the site at
`thecheckdigit.jayde-cork.workers.dev`; the second deploy with routes attached both
hostnames, and every route was checked over HTTPS on each. With `routes` declared and
`workers_dev` absent from the config, the `workers.dev` address is disabled. The
tunnel itself was left in place for `app.thecheckdigit.com`.

## Rollback

`wrangler deploy` keeps previous versions. To roll back:

```bash
npx wrangler rollback
```
