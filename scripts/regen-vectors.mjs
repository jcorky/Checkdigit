// Regenerates tests/vectors/calc_vectors.json from the Python kernel.
// Runs checkdigit/run_pass11.py, which writes the vectors under the OS temp
// directory and prints the path; the file is then copied into tests/vectors.
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
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
copyFileSync(match[1].trim(), target);
console.log(`vectors copied to ${target}`);
