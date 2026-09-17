// Runs the Python test scripts and the acceptance harness for the reference
// implementation under checkdigit/. Fails loudly if Python is missing.
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dir = resolve(here, "..", "checkdigit");
const python = process.env.PYTHON ?? "python3";
const scripts = [
  "test_equipment_checkdigit.py",
  "test_substitution.py",
  "test_contracts.py",
  "test_phase_a.py",
  "test_workspace.py",
  "run_acceptance.py",
];
let failed = 0;
for (const script of scripts) {
  const run = spawnSync(python, [script], {
    cwd: dir,
    encoding: "utf8",
    env: { ...process.env, PYTHONUTF8: "1" },
  });
  if (run.error) {
    console.error(`could not start ${python}: ${run.error.message}`);
    process.exit(1);
  }
  const tail = (run.stdout || "").trim().split(/\r?\n/).slice(-1)[0] ?? "";
  console.log(`${run.status === 0 ? "PASS" : "FAIL"}  ${script}  ${tail}`);
  if (run.status !== 0) {
    failed++;
    process.stdout.write(run.stdout);
    process.stderr.write(run.stderr);
  }
}
process.exit(failed ? 1 : 0);
