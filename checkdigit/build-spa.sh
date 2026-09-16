#!/usr/bin/env bash
# build-spa.sh — compile the single-file React app (checkdigit_app.jsx) into
# static/ so the FastAPI process serves it at "/" (same origin, no CORS).
#
# The app source is ONE file with three deps (react, react-dom, lucide-react).
# This script scaffolds a throwaway Vite project around it, builds, and copies
# the result to ./static/. Re-run it whenever checkdigit_app.jsx changes.
#
#   ./build-spa.sh                 # builds ./static from ./checkdigit_app.jsx
#
# Requires Node 18+ and npm. If you have no Node, you can skip the SPA entirely:
# the API still runs and serves a JSON index at "/", and every function is
# available over HTTP (see /docs). The SPA is the GUI, not the engine.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
SRC="$ROOT/checkdigit_app.jsx"
OUT="$ROOT/static"
BUILD_DIR="$ROOT/.spa-build"

if [ ! -f "$SRC" ]; then
  echo "ERROR: $SRC not found. Run this from the project root." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js not found. Install Node 18+ (https://nodejs.org) or skip" >&2
  echo "       the SPA — the API runs without it and serves JSON at '/'." >&2
  exit 1
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "ERROR: Node 18+ required; found $(node -v)." >&2
  exit 1
fi

echo "==> scaffolding build project in .spa-build/"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/src"

# pin the three runtime deps + Vite/plugin. Versions match the app's API surface.
cat > "$BUILD_DIR/package.json" <<'JSON'
{
  "name": "checkdigit-spa",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": { "build": "vite build" },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "lucide-react": "^0.383.0"
  },
  "devDependencies": {
    "vite": "^5.2.0",
    "@vitejs/plugin-react": "^4.2.0"
  }
}
JSON

cat > "$BUILD_DIR/vite.config.js" <<'JS'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Relative base so the bundle works no matter what path it is served under.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
});
JS

cat > "$BUILD_DIR/index.html" <<'HTML'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CHECKDIGIT — container check-digit correction</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
HTML

cat > "$BUILD_DIR/src/main.jsx" <<'JS'
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./checkdigit_app.jsx";
createRoot(document.getElementById("root")).render(<App />);
JS

# the actual app, copied in verbatim
cp "$SRC" "$BUILD_DIR/src/checkdigit_app.jsx"

echo "==> installing SPA build dependencies (npm)"
( cd "$BUILD_DIR" && npm install --no-audit --no-fund )

echo "==> building"
( cd "$BUILD_DIR" && npm run build )

echo "==> publishing to ./static"
rm -rf "$OUT"
mkdir -p "$OUT"
cp -a "$BUILD_DIR/dist/." "$OUT/"

echo "==> done. static/ now holds the built SPA:"
ls -la "$OUT" | sed 's/^/    /'
echo
echo "FastAPI serves it at '/' automatically when static/ exists."
echo "If running in Docker, rebuild the image (or bind-mount ./static) so the"
echo "container sees it — the Dockerfile has a commented COPY static/ line."
