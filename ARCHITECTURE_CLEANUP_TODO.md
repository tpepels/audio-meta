# Architecture Cleanup TODO

This tracks follow-up cleanup now that the pipeline + app bootstrap architecture is in place.

## High Priority

- [ ] Reduce `audio_meta/daemon.py` size by extracting remaining helper clusters into focused modules (e.g. prompting/defer queue, release scoring, cache helpers).
  - [x] Extract singleton/release-home helpers (`audio_meta/release_home.py`).
  - [x] Extract deferred prompt queue/persistence helpers (`audio_meta/deferred.py`).
  - [x] Extract release scoring/overlap helpers (`audio_meta/release_scoring.py`).
  - [x] Extract directory hint/cache-key helpers (`audio_meta/directory_identity.py`).
  - [x] Extract album batching helpers (`audio_meta/album_batching.py`).
- [x] Remove or refactor “daemon private API” usage from plugins by introducing a small service layer (`audio_meta/services.py`).
- [x] Make `audio_meta/cli.py` thinner by moving subcommand implementations into `audio_meta/commands/` (keep CLI as arg parsing + dispatch only).

## Packaging / Repo Hygiene

- [x] Ensure all new modules/packages are tracked and included in the distribution (`audio_meta/pipeline/`, `audio_meta/assignment.py`, `audio_meta/app.py`).
- [x] Verify no stale imports/reference remain to the removed `audio_meta/pipeline.py`.

## Runtime Boundaries

- [x] Move provider/client construction to `AudioMetaApp` so `AudioMetaDaemon` becomes a pure runtime orchestrator over injected dependencies.
- [x] Decide and document which parts are “runtime only” (watchdog, async workers) vs “business logic” (pipeline stages).

## Audit / Diagnostics

- [x] Add an `audio-meta audit-events --json` output mode and an `--event`/`--since` filter to make it usable for tooling.
- [x] Add a short README section documenting `daemon.pipeline_disable` and `daemon.pipeline_order`.

## Optional

- [x] Add a lightweight “health check” command (e.g. `audio-meta doctor`) that validates config, cache schema, and provider access.
