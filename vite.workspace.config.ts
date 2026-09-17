import { resolve } from "node:path";
import { defineConfig } from "vite";

// The private workspace pages. They are built separately from the public site
// and served by the Python service behind its admin gate (checkdigit/api.py),
// never from the Cloudflare deployment. Output: checkdigit/workspace-ui/.
const root = resolve(import.meta.dirname, "src", "workspace");

export const workspacePages = ["index", "job", "profiles", "connections"] as const;

export default defineConfig({
  root,
  base: "/workspace/",
  publicDir: false,
  build: {
    outDir: process.env.WORKSPACE_UI_OUT ?? resolve(import.meta.dirname, "checkdigit", "workspace-ui"),
    emptyOutDir: true,
    target: "es2022",
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: Object.fromEntries(workspacePages.map((p) => [p, resolve(root, `${p}.html`)])),
    },
  },
});
