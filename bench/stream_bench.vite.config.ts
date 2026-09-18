import { resolve } from "node:path";
import { defineConfig } from "vite";

// Bundles bench/stream_bench.ts for Node; see the header of that file.
export default defineConfig({
  build: {
    ssr: resolve(import.meta.dirname, "stream_bench.ts"),
    outDir: resolve(import.meta.dirname, "runs", "stream_bench"),
    emptyOutDir: true,
    target: "node22",
    minify: false,
    rollupOptions: { output: { entryFileNames: "stream_bench.mjs" } },
  },
});
