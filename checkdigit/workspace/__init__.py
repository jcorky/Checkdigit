"""
Private workspace: durable jobs, staged generations, storage-backed review and
atomic artifact publication over SQLite.

Modules:
  store      schema, connections, migrations
  stream     chunked readers that keep decoder state across boundaries
  jobs       queue with leases, checkpoints, retries and cancellation
  ingest     job runner: register source, stream records, validate, stage
  reconcile  generations, memberships, completeness gates, atomic publish
  review     proposals, selection manifests, approvals, version checks
  export     streamed corrected file, exceptions, ledger, manifest
  api        versioned HTTP routes (mounted by checkdigit/api.py)
  bench      dataset generator, benchmark runner, independent verifier

Nothing here is exposed by the static site. It is a separately configured
capability with its own cost and privacy boundary.
"""
