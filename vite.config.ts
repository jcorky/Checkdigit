import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

const root = resolve(import.meta.dirname, "src");

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
        index: resolve(root, "index.html"),
        "404": resolve(root, "404.html"),
      },
    },
  },
  test: {
    root: import.meta.dirname,
    include: ["tests/**/*.test.ts"],
  },
});
