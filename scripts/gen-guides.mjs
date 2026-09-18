// Generates src/guides/<slug>.html from scripts/guides.data.mjs.
// Run: node scripts/gen-guides.mjs  (also runs from npm run build:guides)
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import { guides, REVIEWED } from "./guides.data.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(root, "src/guides");
mkdirSync(outDir, { recursive: true });

const ORIGIN = "https://thecheckdigit.com";
const FAVICON =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23ffffff'/%3E%3Crect x='5' y='9' width='22' height='14' rx='2.5' fill='none' stroke='%231459B8' stroke-width='2.4'/%3E%3Crect x='19.5' y='9' width='7.5' height='14' rx='2.5' fill='%231459B8' fill-opacity='0.16' stroke='%231459B8' stroke-width='2.4'/%3E%3C/svg%3E";

const titleBySlug = Object.fromEntries(guides.map((g) => [g.slug, g.title]));
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function jsonLd(g) {
  const url = `${ORIGIN}/guides/${g.slug}`;
  const graph = [
    {
      "@type": "TechArticle",
      headline: g.title,
      description: g.description,
      url,
      mainEntityOfPage: url,
      datePublished: REVIEWED,
      dateModified: REVIEWED,
      inLanguage: "en",
      author: { "@type": "Organization", name: "Checkdigit", url: `${ORIGIN}/` },
      publisher: { "@type": "Organization", name: "Checkdigit", url: `${ORIGIN}/` },
    },
    {
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: `${ORIGIN}/` },
        { "@type": "ListItem", position: 2, name: "Reference", item: `${ORIGIN}/reference` },
        { "@type": "ListItem", position: 3, name: g.title, item: url },
      ],
    },
  ];
  return JSON.stringify({ "@context": "https://schema.org", "@graph": graph }, null, 2);
}

function related(g) {
  const items = g.related
    .filter((slug) => titleBySlug[slug])
    .map((slug) => `<li><a href="/guides/${slug}">${esc(titleBySlug[slug])}</a></li>`)
    .join("\n        ");
  if (!items) return "";
  return `      <nav class="related" aria-label="Related guides">
        <h2>Related guides</h2>
        <ul class="related-list">
        ${items}
        </ul>
      </nav>`;
}

function page(g) {
  const url = `${ORIGIN}/guides/${g.slug}`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(g.title)} — Checkdigit</title>
<meta name="description" content="${esc(g.description)}">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#f4f6f8">
<link rel="canonical" href="${url}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Checkdigit">
<meta property="og:title" content="${esc(g.title)}">
<meta property="og:description" content="${esc(g.description)}">
<meta property="og:url" content="${url}">
<meta property="og:image" content="${ORIGIN}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Checkdigit — Container Check Digit Calculator">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(g.title)}">
<meta name="twitter:description" content="${esc(g.description)}">
<meta name="twitter:image" content="${ORIGIN}/og.png">
<link rel="icon" href="${FAVICON}">
<script type="application/ld+json">
${jsonLd(g)}
</script>
<script type="module" src="/pages/shell.ts"></script>
</head>
<body>

<a class="skip-link" href="#main">Skip to content</a>
<header class="bar">
  <div class="wrap row">
    <a class="brand" href="/" aria-label="Checkdigit home">
      <svg class="glyph" viewBox="0 0 28 20" aria-hidden="true">
        <rect x="1" y="1" width="26" height="18" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>
        <rect x="19" y="1" width="8" height="18" rx="3" fill="currentColor" fill-opacity="0.2" stroke="currentColor" stroke-width="2"/>
      </svg>
      Checkdigit
    </a>
    <nav aria-label="Main">
      <a class="navlink" href="/">Calculator</a>
      <details class="menu">
        <summary aria-expanded="false">Tools</summary>
        <div class="menu-panel" aria-label="Tools">
          <a href="/bulk"><span class="mtitle">Bulk check</span><span class="mdesc">Check many numbers at once</span></a>
          <a href="/files"><span class="mtitle">Review a file</span><span class="mdesc">Correct a CSV, EDIFACT, X12 or XML file</span></a>
          <a href="/compare"><span class="mtitle">Compare lists</span><span class="mdesc">Added, removed and conflicting numbers</span></a>
        </div>
      </details>
      <a class="navlink" href="/reference" aria-current="page">Reference</a>
    </nav>
  </div>
</header>

<main class="page guide" id="main">
  <div class="wrap">
    <p class="crumbs"><a href="/">Home</a> · <a href="/reference">Reference</a> · <span aria-current="page">${esc(g.title)}</span></p>
    <h1>${esc(g.title)}</h1>
    <p class="intro">${esc(g.lead)}</p>
    <div class="content">${g.body}
    </div>
    <p class="guide-cta"><a href="/">Open the container check digit calculator →</a></p>
${related(g)}
  </div>
</main>

<footer>
  <div class="wrap row">
    <span class="mono-sm">Checkdigit — ISO 6346, ILU and UIC check digits, verified in your browser</span>
    <span class="mono-sm"><a href="/">Calculator</a> · <a href="/reference">Reference</a></span>
  </div>
</footer>

</body>
</html>
`;
}

let n = 0;
for (const g of guides) {
  writeFileSync(resolve(outDir, `${g.slug}.html`), page(g));
  n++;
}
console.log(`wrote ${n} guide pages to src/guides/`);
