# Pipeline architecture (audio-meta)

This project processes a music library by running each directory through a plugin pipeline. The daemon is responsible for scheduling work (scan/daemon/watchdog, async workers) and the pipeline is responsible for decisions (what release is this, what tags should change, where should files move).

## High-level flow

1. **Directory discovery (runtime)**: `audio_meta/scanner.py` yields `DirectoryBatch` items (directory + audio files).
2. **Directory processing (business logic)**: `audio_meta/pipeline/core.py` runs a fixed sequence of stages; each stage executes one or more plugins from `audio_meta/pipeline/plugins/`.
3. **Side effects (business logic)**:
   - Tag writes: `audio_meta/tagging.py`
   - Moves/cleanup: `audio_meta/organizer.py`
   - Cache/audit updates: `audio_meta/cache.py`

## Where decisions happen

- **Skip policy / staleness**: `audio_meta/pipeline/plugins/directory_skip*.py`, `audio_meta/pipeline/plugins/track_skip.py`
- **Signals & enrichment**: `audio_meta/pipeline/plugins/signal_extractor.py`, `audio_meta/pipeline/plugins/track_enricher.py`
- **Candidate gathering**:
  - MusicBrainz: `audio_meta/pipeline/plugins/candidates_musicbrainz.py`
  - Discogs: `audio_meta/pipeline/plugins/candidates_discogs.py`
- **Release selection**: `audio_meta/pipeline/plugins/release_decision.py` (uses `audio_meta/release_selection.py`)
- **Safety gates (defer/confirm)**:
  - Suspicious fingerprint vs tags: `audio_meta/daemon/core.py`
  - Classical credits review: `audio_meta/daemon/core.py`
- **Singleton handling**: `audio_meta/pipeline/plugins/singleton_handler.py` (uses `audio_meta/release_home.py`)
- **Planning (compute intended writes/moves)**: `audio_meta/pipeline/plugins/planner.py`
- **Unmatched policy / confirmation**: `audio_meta/pipeline/plugins/unmatched_policy.py`
- **Apply plan (side effects)**: `audio_meta/pipeline/plugins/plan_apply.py`
- **Diagnostics / summaries / maintenance**: `audio_meta/pipeline/plugins/directory_diagnostics.py`, `audio_meta/pipeline/plugins/scan_diagnostics.py`, `audio_meta/pipeline/plugins/cache_maintenance.py`

## Customizing the pipeline

`config.sample.yaml` demonstrates two knobs:

- `daemon.pipeline_disable`: list of plugin names to skip.
- `daemon.pipeline_order`: per-stage explicit plugin ordering; any plugins not listed still run after the specified ones.

These are intended for experimentation and troubleshooting; the default order is what the project is tuned for.
