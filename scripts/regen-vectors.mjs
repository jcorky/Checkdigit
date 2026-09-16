// Regenerates tests/vectors/*.json from the Python kernel.
// 1. checkdigit/run_pass11.py writes calc_vectors.json under the OS temp
//    directory and prints the path; the file is copied into tests/vectors.
// 2. scripts/gen_decision_vectors.py writes decision_vectors.json directly.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pythonDir = resolve(here, "..", "checkdigit");
const target = resolve(here, "..", "tests", "vectors", "calc_vectors.json");

const python = process.env.PYTHON ?? "python3";
const run = spawnSync(python, ["run_pass11.py"], { cwd: pythonDir, encoding: "utf8" });

if (run.error) {
  console.error(`could not start ${python}: ${run.error.message}`);
  process.exit(1);
}
process.stdout.write(run.stdout);
process.stderr.write(run.stderr);
if (run.status !== 0) {
  console.error(`run_pass11.py exited with status ${run.status}`);
  process.exit(1);
}

const match = /kernel-truth cases to (.+)$/m.exec(run.stdout);
if (!match || !existsSync(match[1].trim())) {
  console.error("run_pass11.py did not report a vectors path; nothing copied");
  process.exit(1);
}
// Python opens the file in text mode, so on Windows it arrives with CRLF;
// store it with LF so the committed vectors are identical on every platform.
const CRLF = String.fromCharCode(13, 10);
writeFileSync(target, readFileSync(match[1].trim(), "utf8").split(CRLF).join("\n"));
console.log(`vectors copied to ${target}`);

const decisions = spawnSync(python, [resolve(here, "gen_decision_vectors.py")], {
  cwd: resolve(here, ".."),
  encoding: "utf8",
});
process.stdout.write(decisions.stdout);
process.stderr.write(decisions.stderr);
if (decisions.status !== 0) {
  console.error(`gen_decision_vectors.py exited with status ${decisions.status}`);
  process.exit(1);
}
