/*
 * Runs the browser streaming path (src/lib/stream.ts, src/lib/largejob.ts)
 * under Node against a file on disk, with the proposal store and the output
 * as plain files, so the same TypeScript can be measured at sizes the Browser
 * pane's private storage quota cannot hold. Approves every proposal, exports,
 * hashes the output and compares it with the generator's truth file.
 *
 *   npx vite build --config bench/stream_bench.vite.config.ts
 *   node bench/runs/stream_bench/stream_bench.mjs --source bench/fleet_5g.csv --column container --out bench/runs/stream_5g
 */

import { createHash } from "node:crypto";
import { closeSync, createReadStream, fstatSync, ftruncateSync, mkdirSync, openSync, readFileSync, readSync, writeFileSync, writeSync } from "node:fs";
import { cpus, totalmem } from "node:os";
import { basename, join } from "node:path";

import { decideMatching, exportCorrected, RECORD_BYTES, runStream, writeExceptions, writeLedger, type ProposalStore, type Sink } from "../src/lib/largejob";
import type { ByteSource } from "../src/lib/stream";

function arg(name: string, fallback = ""): string {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? (process.argv[i + 1] as string) : fallback;
}

function fileSource(path: string): ByteSource & { close(): void } {
  const fd = openSync(path, "r");
  const size = fstatSync(fd).size;
  return {
    size,
    async slice(start, end) {
      const buf = new Uint8Array(end - start);
      let got = 0;
      while (got < buf.length) {
        const n = readSync(fd, buf, got, buf.length - got, start + got);
        if (!n) break;
        got += n;
      }
      return buf.subarray(0, got);
    },
    close: () => closeSync(fd),
  };
}

/** File-backed store with the same shape as the browser's origin-private file store. */
class FileStore implements ProposalStore {
  private readonly fd: number;
  private size = 0;
  private dec = new Uint8Array(0);

  constructor(path: string) {
    this.fd = openSync(path, "w+");
  }

  get count(): number {
    return Math.floor(this.size / RECORD_BYTES);
  }

  async append(bytes: Uint8Array): Promise<void> {
    writeSync(this.fd, bytes, 0, bytes.length, this.size);
    this.size += bytes.length;
  }

  async flush(): Promise<void> {
    this.grow();
  }

  async read(index: number, n: number): Promise<Uint8Array> {
    const buf = new Uint8Array(n * RECORD_BYTES);
    const got = readSync(this.fd, buf, 0, buf.length, index * RECORD_BYTES);
    return buf.subarray(0, got);
  }

  private grow(): void {
    if (this.dec.length !== this.count) {
      const d = new Uint8Array(this.count);
      d.set(this.dec.subarray(0, Math.min(this.dec.length, d.length)));
      this.dec = d;
    }
  }

  decisions(): Uint8Array {
    this.grow();
    return this.dec;
  }

  async saveDecisions(): Promise<void> {
    /* decisions stay in memory for the benchmark, as in the browser between saves */
  }

  async reset(): Promise<void> {
    ftruncateSync(this.fd, 0);
    this.size = 0;
    this.dec = new Uint8Array(0);
  }

  close(): void {
    closeSync(this.fd);
  }
}

class FileSink implements Sink {
  private readonly fd: number;
  private at = 0;
  constructor(path: string) {
    this.fd = openSync(path, "w");
  }
  async write(bytes: Uint8Array): Promise<void> {
    writeSync(this.fd, bytes, 0, bytes.length, this.at);
    this.at += bytes.length;
  }
  async close(): Promise<void> {
    closeSync(this.fd);
  }
}

function sha256File(path: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    createReadStream(path, { highWaterMark: 8 * 1024 * 1024 }).on("data", (d) => h.update(d)).on("end", () => resolve(h.digest("hex"))).on("error", reject);
  });
}

async function main(): Promise<void> {
  const sourcePath = arg("source");
  const column = arg("column", "container");
  const outDir = arg("out", "bench/runs/stream_bench");
  const truthPath = arg("truth", `${sourcePath}.truth.json`);
  if (!sourcePath) throw new Error("--source <file> is required");
  mkdirSync(outDir, { recursive: true });
  let peakRss = 0;
  let peakHeap = 0;
  const sample = () => {
    const m = process.memoryUsage();
    peakRss = Math.max(peakRss, m.rss);
    peakHeap = Math.max(peakHeap, m.heapUsed + m.arrayBuffers);
  };
  const source = fileSource(sourcePath);
  const store = new FileStore(join(outDir, "records.bin"));
  const log = (msg: string) => process.stdout.write(`${new Date().toISOString().slice(11, 19)} ${msg}\n`);

  log(`inspect ${sourcePath} (${source.size} bytes)`);
  const t0 = performance.now();
  let lastLog = 0;
  const result = await runStream(source, store, {
    columns: [column],
    progress_every_bytes: 64 * 1024 * 1024,
    onProgress: (p) => {
      sample();
      if (p.counts.bytes_done - lastLog >= 512 * 1024 * 1024 || p.phase !== "streaming") {
        lastLog = p.counts.bytes_done;
        const s = (performance.now() - t0) / 1000;
        log(`  ${(p.counts.bytes_done / 2 ** 20).toFixed(0)} MiB, ${p.counts.records} records, ${p.counts.corrected} proposals, ${(p.counts.bytes_done / 2 ** 20 / s).toFixed(1)} MiB/s`);
      }
    },
  });
  const inspectS = (performance.now() - t0) / 1000;
  log(`inspected in ${inspectS.toFixed(1)} s: ${JSON.stringify(result.counts)}`);

  const t1 = performance.now();
  const approval = await decideMatching(store, { kind: "corrected" }, "approved", result.counts.corrected);
  const decideS = (performance.now() - t1) / 1000;
  log(`approved ${approval.count} proposals in ${decideS.toFixed(1)} s`);
  sample();

  const outPath = join(outDir, basename(sourcePath).replace(/(\.[^.]+)?$/, ".corrected$1"));
  const t2 = performance.now();
  let lastExportLog = 0;
  const exported = await exportCorrected(source, result.encoding, store, new FileSink(outPath), {
    onProgress: (done) => {
      sample();
      if (done - lastExportLog >= 1024 * 1024 * 1024) {
        lastExportLog = done;
        log(`  export ${(done / 2 ** 20).toFixed(0)} MiB`);
      }
    },
  });
  const exportS = (performance.now() - t2) / 1000;
  log(`exported ${exported.bytes_written} bytes with ${exported.edits_applied} edits in ${exportS.toFixed(1)} s`);

  const t3 = performance.now();
  const exceptionRows = await writeExceptions(store, new FileSink(join(outDir, "exceptions.csv")));
  const ledgerRows = await writeLedger(store, new FileSink(join(outDir, "ledger.csv")));
  const reportsS = (performance.now() - t3) / 1000;
  sample();

  const t4 = performance.now();
  const outputSha256 = await sha256File(outPath);
  const hashS = (performance.now() - t4) / 1000;
  let truth: Record<string, unknown> | null = null;
  try {
    truth = JSON.parse(readFileSync(truthPath, "utf-8")) as Record<string, unknown>;
  } catch {
    truth = null;
  }
  const expectedSha = truth ? String(truth["expected_corrected_sha256"]) : null;
  const expectedEdits = truth ? Number(truth["expected_corrected_edits"]) : null;
  const record = {
    path: "browser streaming code under Node",
    parser_version: result.parser_version,
    source: { path: sourcePath, size_bytes: source.size, column },
    node: process.version,
    hardware: { cpus: cpus().length, model: cpus()[0]?.model ?? "", ram_bytes: totalmem(), platform: `${process.platform} ${process.arch}` },
    inspect: { seconds: Number(inspectS.toFixed(1)), mib_per_s: Number((source.size / 2 ** 20 / inspectS).toFixed(1)), records_per_s: Math.round(result.counts.records / inspectS), counts: result.counts, encoding: result.encoding, format: result.format },
    decide: { seconds: Number(decideS.toFixed(1)), approved: approval.count, digest: approval.digest },
    export: { seconds: Number(exportS.toFixed(1)), mib_per_s: Number((exported.bytes_written / 2 ** 20 / exportS).toFixed(1)), bytes_written: exported.bytes_written, edits_applied: exported.edits_applied, block_digest: exported.sha256, output_sha256: outputSha256, hash_seconds: Number(hashS.toFixed(1)) },
    reports: { seconds: Number(reportsS.toFixed(1)), exception_rows: exceptionRows, ledger_rows: ledgerRows },
    storage_bytes: { records: store.count * RECORD_BYTES, decisions: store.count },
    peak_rss_bytes: peakRss,
    peak_heap_plus_buffers_bytes: peakHeap,
    node_flags: process.execArgv,
    truth: truth ? { expected_corrected_sha256: expectedSha, expected_corrected_edits: expectedEdits, output_matches: expectedSha === outputSha256, edits_match: expectedEdits === exported.edits_applied } : null,
  };
  writeFileSync(join(outDir, "stream.benchmark.json"), JSON.stringify(record, null, 2) + "\n");
  log(JSON.stringify(record, null, 2));
  store.close();
  source.close();
  if (truth && (expectedSha !== outputSha256 || expectedEdits !== exported.edits_applied)) {
    process.exitCode = 1;
    log("MISMATCH against the truth file");
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
