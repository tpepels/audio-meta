# Refactoring TODO - Clean Architecture Migration

**Last Updated**: 2025-12-19

This document tracks the migration to clean architecture as defined in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Phase 1: Identity & Matching ⏳ IN PROGRESS

**Goal**: Extract identity/canonicalization into clean core layer

### 1.1 Create Core Identity Package Structure
- [ ] Create `audio_meta/core/` directory
- [ ] Create `audio_meta/core/__init__.py`
- [ ] Create `audio_meta/core/identity/` directory
- [ ] Create `audio_meta/core/identity/__init__.py`
- [ ] Create `audio_meta/core/identity/models.py` for domain models

### 1.2 Extract Identity Scanner
- [ ] Create `audio_meta/core/identity/scanner.py`
- [ ] Move `IdentityScanner` class from `identity.py` → `core/identity/scanner.py`
- [ ] Move `IdentityScanResult` and `IdentityCluster` models → `core/identity/models.py`
- [ ] Remove file I/O from scanner (inject file list instead of scanning)
- [ ] Update imports in existing files

### 1.3 Extract Canonicalization Logic
- [ ] Create `audio_meta/core/identity/canonicalizer.py`
- [ ] Move `IdentityCanonicalizer` class → `core/identity/canonicalizer.py`
- [ ] Ensure no cache dependencies in core logic (accept cache as interface)
- [ ] Update imports

### 1.4 Create Matching Module (NEW - Handles Enhancement #1)
- [ ] Create `audio_meta/core/identity/matching.py`
- [ ] Move `_normalize_token()` → `core/identity/matching.py`
- [ ] Move `_merge_substring_clusters()` → `core/identity/matching.py`
- [ ] **ADD**: Implement `_merge_initial_clusters()` for "J.S. Bach" vs "Johann Sebastian Bach"
- [ ] Create `NameMatcher` class with all matching strategies:
  - `exact_match()` - Same normalized token
  - `substring_match()` - One token contains another
  - `initial_match()` - Initials match full name (NEW)
  - `fuzzy_match()` - Levenshtein distance (future)
- [ ] Write unit tests for all matching strategies

### 1.5 Update Identity Scanner to Use Matching Module
- [ ] Refactor `IdentityScanner.scan()` to use `NameMatcher`
- [ ] Apply matching strategies in sequence: exact → substring → initials
- [ ] Add logging for each merge strategy applied

### 1.6 Testing & Verification
- [ ] Create `tests/core/identity/` directory
- [ ] Write unit tests for `NameMatcher` (no I/O)
- [ ] Write unit tests for token normalization
- [ ] Write unit tests for initial matching
- [ ] Verify all identity tests pass
- [ ] Run integration tests with real library

### 1.7 Documentation
- [ ] Update [BUGFIXES.md](BUGFIXES.md) with initial matching fix
- [ ] Update [CANONICAL_EDGE_CASES.md](CANONICAL_EDGE_CASES.md) with coverage status
- [ ] Document `NameMatcher` API in docstrings

---

## Phase 2: Organization Logic

**Goal**: Extract organization logic into clean core layer

### 2.1 Create Core Organization Package
- [ ] Create `audio_meta/core/organization/` directory
- [ ] Create `audio_meta/core/organization/__init__.py`
- [ ] Create `audio_meta/core/organization/models.py`

### 2.2 Extract Path Building Logic
- [ ] Create `audio_meta/core/organization/path_builder.py`
- [ ] Move path construction logic from `Organizer` → `PathBuilder`
- [ ] Separate business rules from file operations
- [ ] Write unit tests for path building

### 2.3 Extract Organization Rules
- [ ] Create `audio_meta/core/organization/organizer.py`
- [ ] Move `_primary_artist()` logic (ensemble detection)
- [ ] Move `_should_organize()` decision logic
- [ ] Remove file system dependencies

### 2.4 Create Organization Service
- [ ] Create `audio_meta/services/organization_service.py`
- [ ] Service orchestrates core logic + file operations
- [ ] Update `organizer.py` to use service pattern

---

## Phase 3: Provider Separation

**Goal**: Isolate external API integrations

### 3.1 MusicBrainz Provider
- [ ] Create `audio_meta/providers/musicbrainz/` directory
- [ ] Create `audio_meta/providers/musicbrainz/client.py`
- [ ] Create `audio_meta/providers/musicbrainz/mapper.py`
- [ ] Create `audio_meta/providers/musicbrainz/models.py`
- [ ] Move MusicBrainz API logic from daemon
- [ ] Create abstract provider interface

### 3.2 Discogs Provider
- [ ] Create `audio_meta/providers/discogs/` directory
- [ ] Create `audio_meta/providers/discogs/client.py`
- [ ] Create `audio_meta/providers/discogs/mapper.py`
- [ ] Create `audio_meta/providers/discogs/models.py`
- [ ] Move Discogs API logic from daemon

### 3.3 AcoustID Provider
- [ ] Create `audio_meta/providers/acoustid/` directory
- [ ] Create client and models
- [ ] Move fingerprinting logic

---

## Phase 4: Infrastructure Layer

**Goal**: Separate technical concerns

### 4.1 Cache Infrastructure
- [ ] Create `audio_meta/infrastructure/cache/` directory
- [ ] Move `MetadataCache` → `infrastructure/cache/metadata_cache.py`
- [ ] Create cache interface (abstract base class)
- [ ] Create SQLite backend implementation
- [ ] Create in-memory backend for testing

### 4.2 Filesystem Infrastructure
- [ ] Create `audio_meta/infrastructure/filesystem/` directory
- [ ] Create `audio_meta/infrastructure/filesystem/scanner.py`
- [ ] Create `audio_meta/infrastructure/filesystem/operations.py`
- [ ] Move file scanning logic
- [ ] Move file operations (move, copy, rename)

### 4.3 Configuration Infrastructure
- [ ] Create `audio_meta/infrastructure/config/` directory
- [ ] Move `Settings` classes → `infrastructure/config/settings.py`
- [ ] Create config models

---

## Phase 5: Service Layer Cleanup

**Goal**: Ensure services are pure application logic

### 5.1 Review Existing Services
- [ ] Review `ReleaseMatchingService` - ensure no business logic
- [ ] Review `TrackAssignmentService` - ensure no business logic
- [ ] Review `ClassicalMusicService` - move heuristics to core
- [ ] Review `DirectoryIdentityService` - move matching to core

### 5.2 Create Missing Services
- [ ] Create `MetadataEnrichmentService`
- [ ] Create `OrganizationService`
- [ ] Create `IdentityService` (wraps core identity logic)

---

## Phase 6: Daemon Refactoring

**Goal**: Make daemon a thin orchestrator

### 6.1 Extract Workflows
- [ ] Create `audio_meta/daemon/workflows/` directory
- [ ] Create `directory_workflow.py` - directory processing workflow
- [ ] Create `track_workflow.py` - track processing workflow
- [ ] Move workflow logic from daemon core

### 6.2 Simplify Daemon Core
- [ ] Daemon only orchestrates workflows
- [ ] Remove business logic from daemon
- [ ] Target: Get daemon under 1,000 lines

---

## Phase 7: CLI Improvements

**Goal**: Separate CLI from business logic

### 7.1 Create CLI Package
- [ ] Create `audio_meta/cli/` directory
- [ ] Create `audio_meta/cli/commands/` directory
- [ ] Move CLI commands to separate modules

### 7.2 Improve CLI UX
- [ ] Better progress reporting
- [ ] Better error messages
- [ ] Add dry-run mode

---

## Completed Work ✅

- [x] Extracted `ReleaseMatchingService` from daemon
- [x] Extracted `TrackAssignmentService` from daemon
- [x] Extracted `ClassicalMusicService` from daemon
- [x] Extracted `DirectoryIdentityService` from daemon
- [x] Fixed canonical name format mismatch (organizer checks identity scanner format)
- [x] Fixed smart artist selection (prefers ensembles)
- [x] Added fuzzy substring matching for name variants

---

## Priority Order

1. **Phase 1** (HIGH) - Identity & Matching + Initial Matching Enhancement
2. **Phase 2** (HIGH) - Organization Logic
3. **Phase 4** (MEDIUM) - Infrastructure Layer
4. **Phase 3** (MEDIUM) - Provider Separation
5. **Phase 5** (MEDIUM) - Service Cleanup
6. **Phase 6** (LOW) - Daemon Refactoring
7. **Phase 7** (LOW) - CLI Improvements

---

## Success Metrics

### Code Quality
- [ ] Core layer has 100% unit test coverage
- [ ] No business logic in infrastructure layer
- [ ] No I/O in core layer
- [ ] All dependencies point inward

### Architecture
- [ ] Clear layer separation
- [ ] Each module has single responsibility
- [ ] Easy to add new providers
- [ ] Easy to swap implementations

### Maintainability
- [ ] New developers can understand architecture quickly
- [ ] Easy to find where logic lives
- [ ] Easy to test individual components
- [ ] Changes are localized to single layer

---

## Notes

- Each task should be a separate commit
- Add tests alongside refactoring
- Maintain backward compatibility
- Document breaking changes
- Update architecture diagrams as you go
