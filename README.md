# Checkdigit

Validation, correction and reference tooling for container and equipment identifiers:
ISO 6346 containers, ILU (EN 13044) units and UIC wagon numbers. The public checker is a
static site that runs entirely in the visitor's browser; nothing is sent to or stored on
a server. The Python service under `checkdigit/` is the reference implementation and the
private processing path; it is not exposed by the static site.

Design direction: Electric Yard (charcoal/teal surfaces, electric lime navigation,
cornflower and warm-orange accents). Tokens live in `src/styles/site.css`.

Documents:

- `PLAN.md`: phases and dependency map.
- `CAPABILITIES.md`: what is implemented, connected, tested and deployed; format catalogue.
- `PARITY.md`: Python symbol to TypeScript symbol, with the proving test.
- `contracts/`: shared entities, enumerations, finding codes and safe defaults.
- `MIGRATION.md`: consumer-visible changes per phase.
- `OPEN_ITEMS.md`: unresolved dependencies and decisions.
- `DEPLOY.md`: deploying the static site.

## Commands

```bash
npm ci
npm test          # parity suite, site checks, wrangler dev smoke
npm run build     # type-check and build to dist/
npm run preview   # serve dist/ with wrangler dev
npm run vectors   # regenerate tests/vectors/*.json from the Python kernel
npm run test:py   # Python kernel, substitution, contract and Phase A tests + acceptance harness
npm run test:all  # both suites
npm run sync      # mirror the working tree to the OneDrive backup folder (manual; not run by the build)
```

Deployment steps are in `DEPLOY.md`; unresolved questions are in `OPEN_ITEMS.md`.

## Copies

- Source of record: `https://github.com/jcorky/Checkdigit` (push `main` after each pass).
- File backup: the OneDrive folder mirrored by `npm run sync`, which also runs from a
  local post-commit hook. It excludes `node_modules`, `dist`, `.wrangler` and `.git`.
