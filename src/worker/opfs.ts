/*
 * Origin-private file system backing for the large-file job. Stored records,
 * decisions and export outputs live on disk inside the browser's private
 * storage for this origin; nothing is sent anywhere, and the job directory is
 * removed when the job is discarded or a new one starts. Sync access handles
 * are only available in dedicated workers, which is where this runs.
 */

import { RECORD_BYTES, type ProposalStore, type Sink } from "../lib/largejob";

export const ROOT_DIR = "checkdigit-large";

export function opfsAvailable(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.storage &&
    typeof navigator.storage.getDirectory === "function" &&
    typeof FileSystemFileHandle !== "undefined" &&
    "createSyncAccessHandle" in FileSystemFileHandle.prototype
  );
}

export async function jobDir(jobId: string): Promise<FileSystemDirectoryHandle> {
  const root = await navigator.storage.getDirectory();
  const base = await root.getDirectoryHandle(ROOT_DIR, { create: true });
  return base.getDirectoryHandle(jobId, { create: true });
}

/** Remove every job directory; called before a new job starts and when a job is discarded. */
export async function removeAllJobs(): Promise<void> {
  try {
    const root = await navigator.storage.getDirectory();
    await root.removeEntry(ROOT_DIR, { recursive: true });
  } catch {
    /* nothing stored */
  }
}

export async function storageEstimate(): Promise<{ quota: number; usage: number } | null> {
  if (typeof navigator === "undefined" || !navigator.storage?.estimate) return null;
  const e = await navigator.storage.estimate();
  return { quota: e.quota ?? 0, usage: e.usage ?? 0 };
}

export class OpfsStore implements ProposalStore {
  private records!: FileSystemSyncAccessHandle;
  private decisionsFile!: FileSystemFileHandle;
  private size = 0;
  private dec = new Uint8Array(0);

  static async open(dir: FileSystemDirectoryHandle): Promise<OpfsStore> {
    const s = new OpfsStore();
    const rec = await dir.getFileHandle("records.bin", { create: true });
    s.records = await rec.createSyncAccessHandle();
    s.size = s.records.getSize();
    s.decisionsFile = await dir.getFileHandle("decisions.bin", { create: true });
    const dh = await s.decisionsFile.createSyncAccessHandle();
    try {
      const n = dh.getSize();
      s.dec = new Uint8Array(s.count);
      if (n) dh.read(s.dec.subarray(0, Math.min(n, s.dec.length)), { at: 0 });
    } finally {
      dh.close();
    }
    return s;
  }

  get count(): number {
    return Math.floor(this.size / RECORD_BYTES);
  }

  async append(bytes: Uint8Array): Promise<void> {
    const n = this.records.write(bytes, { at: this.size });
    this.size += n;
    if (n !== bytes.length) throw new Error("short write to origin-private storage");
  }

  async flush(): Promise<void> {
    this.records.flush();
    this.grow();
  }

  async read(index: number, n: number): Promise<Uint8Array> {
    const buf = new Uint8Array(n * RECORD_BYTES);
    const got = this.records.read(buf, { at: index * RECORD_BYTES });
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
    const h = await this.decisionsFile.createSyncAccessHandle();
    try {
      h.truncate(0);
      h.write(this.dec, { at: 0 });
      h.flush();
    } finally {
      h.close();
    }
  }

  async reset(): Promise<void> {
    this.records.truncate(0);
    this.size = 0;
    this.dec = new Uint8Array(0);
    await this.saveDecisions();
  }

  close(): void {
    try {
      this.records.close();
    } catch {
      /* already closed */
    }
  }
}

/** Streams bytes into a file in the job directory; the result is handed back as a File without loading it. */
export class OpfsSink implements Sink {
  private handle!: FileSystemSyncAccessHandle;
  private fileHandle!: FileSystemFileHandle;
  private at = 0;

  static async open(dir: FileSystemDirectoryHandle, name: string): Promise<OpfsSink> {
    const s = new OpfsSink();
    s.fileHandle = await dir.getFileHandle(name, { create: true });
    s.handle = await s.fileHandle.createSyncAccessHandle();
    s.handle.truncate(0);
    return s;
  }

  async write(bytes: Uint8Array): Promise<void> {
    const n = this.handle.write(bytes, { at: this.at });
    this.at += n;
    if (n !== bytes.length) throw new Error("short write to origin-private storage");
  }

  async close(): Promise<void> {
    this.handle.flush();
    this.handle.close();
  }

  file(): Promise<File> {
    return this.fileHandle.getFile();
  }
}

/** Writes straight to a file the person chose with the browser's save dialog. */
export class WritableSink implements Sink {
  private stream: FileSystemWritableFileStream | null = null;

  constructor(private readonly handle: FileSystemFileHandle) {}

  async write(bytes: Uint8Array): Promise<void> {
    if (!this.stream) this.stream = await this.handle.createWritable();
    await this.stream.write(bytes.slice());
  }

  async close(): Promise<void> {
    if (!this.stream) this.stream = await this.handle.createWritable();
    await this.stream.close();
  }
}
