import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { workspacePages } from "../vite.workspace.config";

/*
 * The workspace pages are a separate build served by the Python service behind
 * its admin gate. They must never land in the public dist, must load nothing
 * from other origins, and may only call the same-origin workspace API.
 */

const root = resolve(import.meta.dirname, "..");
let out = "";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

beforeAll(() => {
  out = mkdtempSync(join(tmpdir(), "cd-workspace-ui-"));
  const run = spawnSync(process.execPath, [resolve(root, "node_modules/vite/bin/vite.js"), "build", "--config", "vite.workspace.config.ts"], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, WORKSPACE_UI_OUT: out },
  });
  if (run.status !== 0) throw new Error(`workspace build failed:\n${run.stdout}\n${run.stderr}`);
});

afterAll(() => {
  rmSync(out, { recursive: true, force: true });
});

describe("workspace pages build", () => {
  it("emits one HTML entry per page under the /workspace/ base", () => {
    for (const page of workspacePages) {
      const file = join(out, `${page}.html`);
      expect(existsSync(file), file).toBe(true);
      const html = readFileSync(file, "utf8");
      expect(html).toContain('src="/workspace/assets/');
      expect(html).toContain('name="robots" content="noindex"');
    }
  });

  it("references no other origin and calls only the same-origin workspace API", () => {
    const files = walk(out).filter((f) => /\.(html|js|css)$/.test(f));
    expect(files.length).toBeGreaterThan(3);
    const external = /https?:\/\/(?!www\.w3\.org\/)[^\s"')]+/g;
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      const hits = (text.match(external) ?? []).filter((u) => !u.startsWith("http://www.w3.org/"));
      expect(hits, `${f} references ${hits.join(", ")}`).toEqual([]);
    }
    const js = files.filter((f) => f.endsWith(".js")).map((f) => readFileSync(f, "utf8")).join("\n");
    expect(js).toContain("/v1/workspaces/");
    expect(js).toMatch(/credentials:["'`]same-origin["'`]/);
  });

  it("uses the shared design tokens from the site stylesheet", () => {
    const css = walk(out).filter((f) => f.endsWith(".css")).map((f) => readFileSync(f, "utf8")).join("\n");
    for (const token of ["--bg:#f4f6f8", "--action-bg:#1459b8", "--ok-bg:", "--warn-bg:", "--err-bg:"]) {
      expect(css.replace(/\s+/g, ""), token).toContain(token);
    }
  });

  it("is absent from the public site build inputs", () => {
    const publicConfig = readFileSync(resolve(root, "vite.config.ts"), "utf8");
    expect(publicConfig).not.toContain("workspace");
    const dist = resolve(root, "dist");
    if (existsSync(dist)) {
      for (const page of workspacePages) {
        if (page === "index") continue;
        expect(existsSync(join(dist, `${page}.html`)), `${page}.html must not be in dist`).toBe(false);
      }
    }
  });
});
