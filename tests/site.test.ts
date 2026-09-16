import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { pages } from "../vite.config";
import {
  loadVectors,
  policiesFrom,
  runCalcVectors,
  runDecisionCases,
  type CalcVector,
  type DecisionVectors,
  type KernelLike,
} from "./helpers/vectors";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const srcDir = resolve(root, "src");

/** Routes the site must answer. Every page except the 404 page is a route. */
const routes = pages.filter((p) => p !== "404").map((p) => (p === "index" ? "/" : `/${p}`));

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

const read = (p: string) => readFileSync(p, "utf8");

beforeAll(async () => {
  await build({ configFile: resolve(root, "vite.config.ts"), logLevel: "silent" });
  if (!existsSync(dist)) throw new Error("vite build produced no dist/ directory");
});

describe("built output", () => {
  const files = () => walk(dist).map((p) => ({ path: p, rel: relative(dist, p).replace(/\\/g, "/") }));

  it("emits one HTML file per page", () => {
    for (const page of pages) {
      expect(existsSync(resolve(dist, `${page}.html`)), `${page}.html`).toBe(true);
    }
  });

  it("references no external scripts, styles, images, fonts or frames", () => {
    const external = /^(https?:)?\/\//i;
    const offenders: string[] = [];
    for (const f of files()) {
      const text = read(f.path);
      if (f.rel.endsWith(".html")) {
        const tags = text.matchAll(/<(script|link|img|iframe|source|video|audio|object|embed|use)\b[^>]*>/gi);
        for (const tag of tags) {
          const attrs = tag[0].matchAll(/\b(?:src|href|srcset|xlink:href|data)\s*=\s*["']([^"']*)["']/gi);
          for (const a of attrs) {
            if (external.test(a[1] as string)) offenders.push(`${f.rel}: ${tag[0]}`);
          }
        }
        if (/<meta[^>]+http-equiv\s*=\s*["']refresh/i.test(text)) offenders.push(`${f.rel}: meta refresh`);
      }
      if (f.rel.endsWith(".css") || f.rel.endsWith(".html")) {
        for (const m of text.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
          if (external.test(m[1] as string)) offenders.push(`${f.rel}: url(${m[1]})`);
        }
        for (const m of text.matchAll(/@import\s+(?:url\()?["']?([^"')\s;]+)/gi)) {
          if (external.test(m[1] as string)) offenders.push(`${f.rel}: @import ${m[1]}`);
        }
      }
      if (f.rel.endsWith(".js")) {
        // URL-like strings in scripts are resource loads unless they are the href of
        // a plain anchor in rendered markup (reference links the user chooses to follow).
        for (const m of text.matchAll(/(https?:)?\/\/[a-z0-9.-]+\.[a-z]{2,}[^"'`\s<>]*/gi)) {
          const before = text.slice(Math.max(0, m.index - 24), m.index);
          if (/<a\s[^>]*href=["']$/i.test(before)) continue;
          if (/^["'`]/.test(text.slice(m.index - 1, m.index)) || /["'`]/.test(text.slice(m.index + m[0].length, m.index + m[0].length + 1))) {
            offenders.push(`${f.rel}: ${m[0]}`);
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("contains no network calls", () => {
    const apis = /\b(fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|importScripts)\b/;
    const offenders: string[] = [];
    for (const f of files()) {
      if (!f.rel.endsWith(".js") && !f.rel.endsWith(".html")) continue;
      if (f.rel === "sw.js") continue; // the service worker's own asset caching is checked separately
      const text = read(f.path);
      const m = apis.exec(text);
      if (m) offenders.push(`${f.rel}: ${m[0]} at ${m.index}`);
    }
    expect(offenders).toEqual([]);
  });

  it("keeps the first-load JavaScript for / under 60 kB gzipped", async () => {
    const { gzipSync } = await import("node:zlib");
    const index = read(resolve(dist, "index.html"));
    const scripts = [...index.matchAll(/<script[^>]+src=["']([^"']+)["']/g)].map((m) => m[1] as string);
    const preloads = [...index.matchAll(/<link[^>]+rel=["']modulepreload["'][^>]+href=["']([^"']+)["']/g)].map(
      (m) => m[1] as string,
    );
    let total = 0;
    for (const s of [...scripts, ...preloads]) {
      total += gzipSync(readFileSync(resolve(dist, s.replace(/^\//, "")))).length;
    }
    expect(scripts.length + preloads.length).toBeGreaterThan(0);
    expect(total).toBeLessThan(60 * 1024);
  });
});

describe("one kernel, no drift", () => {
  it("has exactly one CALC-PURE block in the source, in the shared module", () => {
    const holders = walk(srcDir).filter((p) => read(p).includes("CALC-PURE-BEGIN"));
    expect(holders.map((p) => relative(root, p).replace(/\\/g, "/"))).toEqual(["src/lib/checkdigit.ts"]);
    const text = read(resolve(srcDir, "lib/checkdigit.ts"));
    expect(text.split("CALC-PURE-BEGIN").length).toBe(2);
    expect(text.split("CALC-PURE-END").length).toBe(2);
  });

  it("ships the letter table exactly once in the build", () => {
    const literal = /10,\s*12,\s*13,\s*14,\s*15,\s*16,\s*17,\s*18,\s*19,\s*20,\s*21,\s*23/;
    // A Web Worker is a separate compilation unit, so its bundle carries its own
    // copy built from the same source module; every page chunk shares one copy.
    const js = walk(dist).filter((p) => p.endsWith(".js") && !/inspect\.worker-/.test(p));
    const holders = js.filter((p) => literal.test(read(p)));
    expect(holders.length, holders.join(", ")).toBe(1);
    expect(relative(dist, holders[0] as string)).toMatch(/^assets[\\/]checkdigit-[\w-]+\.js$/);
    const workers = walk(dist).filter((p) => /inspect\.worker-/.test(p));
    expect(workers.length).toBe(1);
    expect(literal.test(read(workers[0] as string))).toBe(true);
  });

  it("built kernel chunk reproduces every kernel vector", async () => {
    const chunk = walk(resolve(dist, "assets")).find((p) => /checkdigit-[\w-]+\.js$/.test(p));
    if (!chunk) throw new Error("built kernel chunk not found under dist/assets");
    const built = (await import(pathToFileURL(chunk).href)) as unknown as KernelLike;
    for (const name of ["explain", "correctIdentifier", "correctX12Equipment"] as const) {
      expect(typeof built[name], name).toBe("function");
    }
    runCalcVectors(built, loadVectors<CalcVector[]>("calc_vectors.json"));
    const data = loadVectors<DecisionVectors>("decision_vectors.json");
    runDecisionCases(built, data.cases, policiesFrom(data));
  });
});

describe("design tokens", () => {
  const hex = (h: string) => {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const lum = (h: string) => {
    const [r, g, b] = hex(h).map((c) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a: string, b: string) => {
    const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
    return (l1 + 0.05) / (l2 + 0.05);
  };

  it("keeps text at or above 4.5:1 on both palettes", () => {
    // Electric Yard tokens (src/styles/site.css). Text colours are checked on
    // every surface they appear on; decorative blocks carry their own text token.
    const dark = { bg: "#172229", panel: "#24343c", raised: "#2d4048" };
    const darkText = ["#e8f3e9", "#afc7c6", "#bce962", "#7cd9a2", "#f0a97e", "#f58a7e", "#9fb6ec", "#a9babb"];
    const light = { bg: "#f2f4e9", panel: "#fffff8", raised: "#e9ecde" };
    const lightText = ["#1c363b", "#4f6a6d", "#1e6b45", "#8c4a15", "#a9382b", "#2b4fa8", "#4f6164"];
    const failures: string[] = [];
    for (const [name, surface] of Object.entries(dark)) {
      for (const t of darkText) if (contrast(t, surface) < 4.5) failures.push(`dark ${t} on ${name} = ${contrast(t, surface).toFixed(2)}`);
    }
    for (const [name, surface] of Object.entries(light)) {
      for (const t of lightText) if (contrast(t, surface) < 4.5) failures.push(`light ${t} on ${name} = ${contrast(t, surface).toFixed(2)}`);
    }
    // action buttons and decorative blocks
    for (const [fg, bg, label] of [
      ["#21332b", "#bce962", "dark primary action"],
      ["#ffffff", "#145c63", "light primary action"],
      ["#17373c", "#bce962", "text on lime (dark)"],
      ["#17373c", "#ceef77", "text on lime (light)"],
      ["#17373c", "#e79768", "text on orange (dark)"],
      ["#17373c", "#f9a572", "text on orange (light)"],
      ["#17373c", "#7d9feb", "text on blue (light)"],
    ] as const) {
      if (contrast(fg, bg) < 4.5) failures.push(`${label} = ${contrast(fg, bg).toFixed(2)}`);
    }
    expect(failures).toEqual([]);
  });

  it("keeps brand and semantic tokens separate in the stylesheet", () => {
    const css = read(resolve(srcDir, "styles/site.css")).toLowerCase();
    for (const [token, value] of Object.entries({
      "--bg": "#172229",
      "--panel": "#24343c",
      "--text": "#e8f3e9",
      "--muted": "#afc7c6",
      "--line": "#42595c",
      "--lime": "#bce962",
      "--action-bg": "#bce962",
      "--action-text": "#21332b",
      "--blue": "#718fd2",
      "--orange": "#e79768",
      "--on-decor": "#17373c",
    })) {
      expect(css, token).toContain(`${token}: ${value};`);
    }
    // lime is a brand colour, never the meaning of "valid"
    const ok = /--ok: (#[0-9a-f]{6});/.exec(css)?.[1];
    const err = /--err: (#[0-9a-f]{6});/.exec(css)?.[1];
    expect(ok).toBeDefined();
    expect(ok).not.toBe("#bce962");
    expect(err).not.toBe("#bce962");
    expect(css).toContain(".badge.valid, .badge.passed { color: var(--ok-text)");
  });
});

describe("wrangler dev serves every route", () => {
  const port = 8798;
  const base = `http://127.0.0.1:${port}`;
  let child: ChildProcess | undefined;

  const stop = () => {
    if (!child?.pid) return;
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      // workerd is not always in the node process tree on Windows; also kill
      // whatever is still listening on the test port so no orphan survives.
      const netstat = spawnSync("netstat", ["-ano"], { encoding: "utf8" }).stdout ?? "";
      const pids = new Set<string>();
      for (const line of netstat.split(/\r?\n/)) {
        const m = /^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$/.exec(line);
        if (m && Number(m[1]) === port) pids.add(m[2] as string);
      }
      for (const pid of pids) spawnSync("taskkill", ["/pid", pid, "/F"], { stdio: "ignore" });
    } else {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {
        child.kill("SIGTERM");
      }
    }
    child = undefined;
  };

  beforeAll(async () => {
    const wrangler = resolve(root, "node_modules/wrangler/bin/wrangler.js");
    if (!existsSync(wrangler)) throw new Error("wrangler is not installed; run npm ci");
    const log: string[] = [];
    child = spawn(process.execPath, [wrangler, "dev", "--port", String(port), "--ip", "127.0.0.1"], {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
      env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "1" },
    });
    child.stdout?.on("data", (d: Buffer) => log.push(d.toString()));
    child.stderr?.on("data", (d: Buffer) => log.push(d.toString()));
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(`${base}/`);
        if (res.status === 200) return;
      } catch {
        // not up yet
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    stop();
    throw new Error(`wrangler dev did not answer on ${base} within 90 s:\n${log.join("")}`);
  });

  afterAll(stop);

  for (const route of routes) {
    it(`GET ${route} is 200 text/html`, async () => {
      const res = await fetch(`${base}${route}`);
      expect(res.status).toBe(200);
      expect(res.headers.get("content-type") ?? "").toContain("text/html");
      const body = await res.text();
      expect(body).toContain("Checkdigit");
    });
  }

  it("answers an unknown path with the site's own 404 page", async () => {
    const res = await fetch(`${base}/no-such-page`);
    expect(res.status).toBe(404);
    expect(await res.text()).toContain("No page at this address");
  });
});
