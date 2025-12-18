# Refactoring TODO (Step-by-step)

Keep refactors behavior-preserving unless explicitly called out. After every slice: `ruff`, `mypy`, `unittest` green.

## Next: shrink `TrackMetadata.extra` state soup

Goal: fewer magic strings and less cross-stage implicit coupling.

- [ ] Centralize commonly-used `extra` keys in one module (`TRACKNUMBER`, `DISCNUMBER`, `TRACK_TOTAL`, `MATCH_SOURCE`, …).
- [ ] Replace scattered string literals with constants + small helpers.
- [ ] Promote stable keys to typed fields on `TrackMetadata` (or a dedicated struct).
- [ ] Add migration glue in tag reading / enrichment to keep backward compatibility.
- [ ] Add unit tests around serialization/export fixtures once fields move.

## Next: finish prompt separation (IO vs logic)

Goal: selection logic unit-testable without patching `input()`.

- [ ] Extract the interactive `input()` loop behind a small interface (e.g. `PromptIO`) so tests can run without monkeypatching builtins.
- [ ] Add tests for “invalid choice → reprompt” without patching `input()`.

## Next: transactional side effects (remaining)

- [ ] Add tests for EXDEV move behavior and move-failure behavior.

## Next: album-level decision vs track-level assignment

- [ ] Make directory-level release selection independent from already-matched single tracks.
- [ ] Make assignment diagnostics first-class (coverage, consensus, conflict reasons).
- [ ] Add exported fixtures from real-world ambiguous albums (Chopin Etudes, opera arias, multi-disc).

## Next: commands + UX

- [ ] Centralize common command output patterns (header/summary formatting).
- [ ] Extend `doctor` further: warn on large deferred queue, organizer enabled without target_root, etc.

## New discoveries (keep in mind)

- [ ] Assignment matrices can be “wide” (more release tracks than files). Ensure assignment helpers never return padded dummy rows (fixed in code; keep a regression test).
- [ ] Prompt candidate expansion can produce lots of `score 0.00` noise; consider filtering/thresholds in a follow-up UX pass.

## Done recently (for history)

- Removed `daemon._*` usage from `audio_meta/pipeline/plugins/*` by routing through `AudioMetaServices`.
- Extracted prompt option building into `audio_meta/release_prompt.py` (+ tests).
- Improved plan apply semantics: return success/failure; avoid caching bindings on apply failure.
- Decoupled commands (`audio_meta/fs_utils.py`) and enhanced `doctor` diagnostics (+ tests).
