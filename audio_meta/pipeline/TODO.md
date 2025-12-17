# Pipeline Migration TODO

This tracks remaining work to move business logic out of `audio_meta/daemon.py` into pipeline stages.

## Next

- [x] Directory skip policy (directory hash unchanged) is in pipeline.
- [x] Directory init (cached release injection) is in pipeline.
- [x] Release decision (including no-candidate manual selection/defer) is in pipeline.
- [x] Release finalize (apply/persist/summary) is in pipeline.

## In Progress

- [x] Track/file skip policy
  - [x] already processed + unchanged → skip
  - [x] stale moved copy → skip
  - [x] organizer enabled changed → reprocess
  - [x] expose skip reason to diagnostics/audit (`DirectoryContext.diagnostics["skipped_tracks"]`)

## Planned Stages

- [x] Planning stage: build `PlannedUpdate` from `(meta, match, tags, organizer rules)` including classical adaptation + naming rules.
- [x] Singleton handling stage: release-home selection, move gating, conflict detection.
  - [x] release-home selection (uses existing gating/conflict helpers)
  - [x] per-track target override as a plugin stage (`singleton_target_override`)
- [x] Caching stage: directory-hash → release binding, processed-file bookkeeping, release-home index maintenance, staleness pruning.
  - [x] directory-hash → release binding (hash cache)
  - [x] release-home index maintenance (non-singleton homes)
  - [x] processed-file bookkeeping (plan apply hook)
  - [x] staleness pruning scheduling (post-scan cache maintenance)
- [x] Post-decision/diagnostics stage: warning summaries, “top candidates for unmatched” logging, audit trail entries.
  - [x] “no match in directory” skip reason
  - [x] warning summaries (scan-level, counts warning log lines)
  - [x] audit trail entries (writes scan completion + per-directory events to cache)
  - [x] directory already-processed skip moved to pipeline
  - [x] store audit events in dedicated table (`audit_events`)

## Remaining

- [x] Plugin configuration/ordering (beyond disable list) via config (`daemon.pipeline_order`).
- [x] Dedicated audit querying/reporting command (read from `audit_events`) (`audio-meta audit-events`).
- [x] Include `DirectoryContext.diagnostics["skip_reason"]` + skipped track counts in audit payloads consistently.
