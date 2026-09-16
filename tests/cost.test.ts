import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const here = resolve(import.meta.dirname, "..");
const configPath = resolve(here, "wrangler.jsonc");

function loadJsonc(path: string): Record<string, unknown> {
  const raw = readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  return JSON.parse(raw) as Record<string, unknown>;
}

const config = loadJsonc(configPath);

const bindingKeys = [
  "d1_databases",
  "kv_namespaces",
  "r2_buckets",
  "durable_objects",
  "queues",
  "analytics_engine_datasets",
  "services",
  "ai",
  "vectorize",
  "hyperdrive",
  "browser",
  "send_email",
  "workflows",
  "pipelines",
  "dispatch_namespaces",
  "mtls_certificates",
  "tail_consumers",
  "triggers",
  "vars",
];

describe("wrangler.jsonc stays on the free static-assets path", () => {
  it("declares no bindings", () => {
    for (const key of bindingKeys) {
      expect(config, `unexpected binding key "${key}"`).not.toHaveProperty(key);
    }
  });

  it("declares no Worker script", () => {
    expect(config).not.toHaveProperty("main");
    expect(config).not.toHaveProperty("assets.run_worker_first");
  });

  it("serves static assets from the build directory", () => {
    const assets = config["assets"] as Record<string, unknown>;
    expect(assets).toBeDefined();
    expect(assets["directory"]).toBe("./dist");
    expect(assets["not_found_handling"]).toBe("404-page");
    expect(assets).not.toHaveProperty("binding");
  });

  it("does not declare any environment overrides", () => {
    expect(config).not.toHaveProperty("env");
  });
});
