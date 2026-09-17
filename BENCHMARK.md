# Benchmark: three-million-record fleet file

Recorded run of the workspace path (`checkdigit/workspace/`) on 2026-09-16. Raw
results: `bench/results/r3m.benchmark.json`, independent verification
`bench/results/r3m.verify.json`, dataset truth `bench/results/fleet_3m.truth.json`.
A 100,000-record run of the same procedure is in `bench/results/r100k.benchmark.json`.

## Reproduce

```bash
cd checkdigit
python3 -m workspace.bench generate --records 3000000 --out ../bench/fleet_3m.csv
python3 -m workspace.bench run --dataset ../bench/fleet_3m.csv --root ../bench/runs/r3m --crash-after 1000000
python3 -m workspace.bench verify --dataset ../bench/fleet_3m.csv --root ../bench/runs/r3m
```

The generator is seeded (`--seed 20260916`); the same arguments produce the same
bytes, the same category counts and the same expected output hash. The dataset files
are not committed (150 MB); the truth files are.

## Hardware and software

| Item | Value |
|---|---|
| Machine | Windows 11 Enterprise 10.0.26200, AMD64 |
| Processor | Intel64 Family 6 Model 183 (Raptor Lake class), 28 logical processors |
| Memory | 63.7 GiB |
| Storage | local disk; device type not recorded by the run |
| Python | 3.13.14 |
| SQLite | 3.50.4, WAL, `synchronous=NORMAL`, 256 MiB page cache per connection |
| Workers | one worker thread at a time for the main load (plus the crashed attempt); the concurrent-publisher and cancellation scenarios use additional threads with their own connections |

## Dataset

| Item | Value |
|---|---|
| Records | 3,000,000 data rows plus a header |
| Bytes | 149,937,153 (UTF-8; mixed CRLF and LF; quoted fields with embedded newlines, escaped quotes, accented, CJK and emoji text, 150-character remarks) |
| Columns | `ref,container,size_type,gross_kg,remark`; identifier column mapped as `container` |
| Valid check digits | 2,549,703 rows generated as valid; 2,594,768 rows pass the kernel (duplicates and valid-looking body changes also pass) |
| Wrong check digit | 240,345 generated, plus 89,882 body changes of which most break the digit, plus 14,943 conflicts: 345,170 correctable rows in total |
| Missing identifier | 29,834 |
| Junk identifiers | 30,228 |
| Duplicates of an earlier row | 45,065 (same identifier, different attributes) |
| Distinct identifiers accepted into the fleet | 2,738,243 |

## Recorded results

| Step | Result |
|---|---|
| Register source (hash, size, codec sniff, copy into the immutable store) | 0.3 s |
| Ingest and validate (stream, kernel per cell, observations, memberships, findings), including the simulated crash at 1,000,000 records, lease expiry and resume by a second worker | 439.8 s |
| Throughput over that step | 6,821 records/s, 0.33 MiB/s |
| Peak working set of the benchmark process | 1,047,523,328 bytes (999 MiB) |
| Database after the run (`workspace.db`, checkpointed) | 1,631,711,232 bytes |
| Immutable source store | 149,947,146 bytes |
| Artifacts (two full exports: the retried interrupted build and the timed build) | 368,850,000 bytes |
| Review page, corrected only (200 rows, 25 consecutive pages) | p50 2.21 ms, p95 2.90 ms |
| Review page, prefix `MSKU` | p50 2.40 ms, p95 3.44 ms |
| Review page, invalid structure | p50 3.06 ms, p95 3.67 ms |
| Review page, unfiltered | p50 0.76 ms, p95 0.79 ms |
| Count of corrected proposals (345,170) | 36 ms |
| Publish the main generation (2,738,243 fleet rows written in one transaction) | 5.2 s |
| 200-record incremental delta: ingest and compare against the published fleet | 1.33 s |
| Publish the delta | 0.078 s |
| Approve every corrected proposal through a storage-backed selection (345,170 members frozen with versions and digested) | 13.0 s |
| Interrupted export (fails after 1,000 edits) plus full retry under the same key | 8.4 s |
| Timed export of the corrected file, exceptions CSV, ledger CSV and manifest | 6.95 s |
| Whole run | 483.6 s |

## Scenarios recorded in the same run

| Scenario | Evidence |
|---|---|
| Restart after a crash | The first worker died after committing 1,000,000 records (`simulated crash after 1000000 records`). After its 0.5 s lease expired a second worker claimed the job, resumed from the checkpoint and finished. Observations equal records (3,000,000) and every counter equals the independent recount, so no record was counted twice or skipped. |
| Cancellation | A second job on the same file was cancelled two seconds after it started; the worker stopped 0.19 s after the request with 65,000 records committed and the job in state `cancelled`. |
| Incremental delta leaves the rest untouched | Effects: added 200, retired 0. After publication 2,738,243 fleet rows still carry the main generation's id and 200 carry the delta's; fleet count 2,738,443. |
| Two publishers cannot both overwrite | Two 50-record generations pinned to the same baseline were published from two threads at once: one `published`, one `BASELINE_CONFLICT`. Fleet count afterwards is baseline + 50, not + 100. |
| Replay applies once | Re-issuing the winning publish with its operation id answered `replayed: true` and changed nothing. |
| Interrupted export never served | The interrupted build left the artifact in state `failed` with no hash; the retry under the same idempotency key produced a `ready` artifact with the same hash as the timed export. |
| Exported bytes | SHA-256 `0688393590b711208da960f4fe35aa376da75943bc7ffedbce97b6e6436f3a36`, identical to the hash the generator computed for "every correctable row corrected", with 345,170 edits applied and 60,062 exceptions listed. |
| Identity collisions | 189,208 identifiers appear more than once with different attributes; the finding is recorded (`IDENTITY_COLLISION`, record scope) and the first observation of each identifier is the membership. Publication is not blocked by a record-scope finding. |

## Independent verification

`workspace.bench verify` re-reads the raw CSV with the standard library reader and the
kernel only, and hashes the exported file. It agrees with the run on records
(3,000,000), valid (2,594,768), corrected (345,170), invalid structure (30,228),
missing (29,834), edits applied (345,170), distinct fleet keys (2,738,243) and on both
output hashes.

## What this does and does not show

- The three-million-record requirement is met for a CSV fleet file on one host with
  SQLite: ingest, review, approval, export, publication and a delta all complete with
  verified counts and bytes.
- The first attempt at this run used SQLite's default 2 MiB page cache and slowed from
  about 20,000 to under 5,000 records per second once the random-order key indexes
  outgrew the cache. The cache was raised to 256 MiB per connection and the run was
  repeated; the figures above are from the repeated run. The first attempt's numbers
  were not kept because it was stopped before completing.
- The ingest rate includes a full re-read of the first million records after the crash
  (resumption skips committed records instead of seeking, see `OPEN_ITEMS.md`). The
  100,000-record run without the extra rescan sustained 25,767 records/s.
- Ingest is single-threaded Python: the kernel, the CSV state machine and SQLite
  inserts share one core. Partitioning a file across workers is possible with the
  lease protocol but was not measured.
- Memory: the process peaked just under 1 GiB, most of it SQLite page cache across the
  connections the benchmark opens (main, crashed worker, race and cancellation threads).
  The streaming readers themselves hold one chunk plus one batch of rows.
- Not recorded: disk pressure (out-of-space), a multi-process worker fleet, XML or X12
  at this size (not on the streaming path), and the same run on a smaller machine.
