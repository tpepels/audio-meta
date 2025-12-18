# Refactoring TODO (Step-by-step)

Keep refactors behavior-preserving unless explicitly called out. After every slice: `ruff`, `mypy`, `unittest` green.

## Next: finish shrinking `TrackMetadata.extra`

Goal: fewer cross-stage implicit keys; `extra` stays for genuinely ad-hoc tags.

- [x] Identify hot `extra` keys used across pipeline (`TRACKNUMBER`, `DISCNUMBER`, `TRACK_TOTAL`, `MATCH_SOURCE`).
- [x] Promote stable keys to typed fields on `TrackMetadata` (`track_number`, `disc_number`, `track_total`, `match_source`).
- [x] Add migration glue (accept legacy `extra` values and tag strings like `03/12`).
- [x] Update tag writing/diff to treat track/disc numbers as first-class.
- [ ] Decide what to do with remaining “semi-structured” keys currently stored in `extra` (e.g. `DISCOGS_RELEASE_ID`).

## Next: finish prompt separation (IO vs logic)

Goal: selection logic unit-testable without patching `input()`.

- [ ] Extract the interactive `input()` loop behind a small interface (e.g. `PromptIO`) so tests can run without monkeypatching builtins.
- [ ] Add tests for “invalid choice → reprompt” without patching `input()`.

## Next: daemon decomposition

Goal: reduce the size/surface area of `AudioMetaDaemon` and isolate responsibilities.

- [ ] Split worker lifecycle (scan/daemon/watchdog) into a small runtime module.
- [ ] Split prompt rendering/helpers (preview building, option rendering) into `audio_meta/daemon/prompting_*`.
- [ ] Split matching/enrichment helpers into focused modules (keep pipeline as the primary decision engine).

## Next: transactional side effects (remaining)

- [ ] Add tests for EXDEV move behavior and move-failure behavior.

## Next: album-level decision vs track-level assignment

- [x] Feed directory-level tag hints into release scoring even when tracks are skipped (reduces “pending_results empty” bias).
- [ ] Make directory-level release selection fully independent from already-matched single tracks (avoid per-track matches dominating the album choice).
- [ ] Make assignment diagnostics first-class (coverage, consensus, conflict reasons).
- [ ] Add exported fixtures from real-world ambiguous albums (Chopin Etudes, opera arias, multi-disc).

## Next: commands + UX

- [ ] Centralize common command output patterns (header/summary formatting).
- [ ] Extend `doctor` further: warn on large deferred queue, organizer enabled without target_root, etc.

## New discoveries (keep in mind)

- [ ] Assignment matrices can be “wide” (more release tracks than files). Ensure assignment helpers never return padded dummy rows (fixed in code; keep a regression test).
- [ ] Prompt candidate expansion can produce lots of `score 0.00` noise; consider filtering/thresholds in a follow-up UX pass.
- [ ] Prompt previews should never crash on missing directories/unreadable tags; keep defensive guards in the preview path.

## Done recently (for history)

- Removed `daemon._*` usage from `audio_meta/pipeline/plugins/*` by routing through `AudioMetaServices`.
- Extracted prompt option building into `audio_meta/release_prompt.py` (+ tests).
- Improved plan apply semantics: return success/failure; avoid caching bindings on apply failure.
- Decoupled commands (`audio_meta/fs_utils.py`) and enhanced `doctor` diagnostics (+ tests).
- Centralized common tag keys in `audio_meta/meta_keys.py` and replaced magic strings.
- Converted `audio_meta/daemon.py` into package `audio_meta/daemon/` (`core.py` + re-export in `__init__.py`).
- Added a “Sample tracks” preview section to confirmation prompts.
- Reduced `TrackMetadata.extra` by promoting common keys to typed fields (+ migration + tests).
