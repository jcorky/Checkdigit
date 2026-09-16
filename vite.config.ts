import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

const root = resolve(import.meta.dirname, "src");

export const pages = ["index", "check", "files", "bulk", "compare", "reference", "404"] as const;

export default defineConfig({
  root,
  publicDir: resolve(import.meta.dirname, "public"),
  build: {
    outDir: resolve(import.meta.dirname, "dist"),
    emptyOutDir: true,
    target: "es2022",
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: {
        ...Object.fromEntries(pages.map((p) => [p, resolve(root, `${p}.html`)])),
        // The kernel is its own entry so the build emits one shared chunk with
        // its export names intact; tests/site.test.ts runs the parity vectors
        // against that built chunk to prove the pages ship the same code.
        checkdigit: resolve(root, "lib/checkdigit.ts"),
      },
      preserveEntrySignatures: "strict",
    },
  },
  test: {
    root: import.meta.dirname,
    include: ["tests/**/*.test.ts"],
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
