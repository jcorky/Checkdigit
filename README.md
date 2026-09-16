# CHECKDIGIT

Verifies and corrects check digits on shipping-equipment identifiers: ISO 6346
containers, ILU (EN 13044) units and UIC wagon numbers. The site is static and runs
entirely in the visitor's browser; nothing is sent to or stored on a server.

The Python tree in `checkdigit/` is the reference implementation. Everything the
site does is a port of it, proven by the tests listed in `PARITY.md`.

## Commands

```bash
npm ci
npm test          # parity suite, site checks, wrangler dev smoke
npm run build     # type-check and build to dist/
npm run preview   # serve dist/ with wrangler dev
npm run vectors   # regenerate tests/vectors/*.json from the Python kernel
npm run sync      # mirror the working tree to the OneDrive backup folder
```

Deployment steps are in `DEPLOY.md`; unresolved questions are in `OPEN_ITEMS.md`.

## Copies

- Source of record: `https://github.com/jcorky/Checkdigit` (push `main` after each pass).
- File backup: the OneDrive folder mirrored by `npm run sync`, which also runs from a
  local post-commit hook. It excludes `node_modules`, `dist`, `.wrangler` and `.git`.
