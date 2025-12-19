# Audio Meta - Development Tracking

**Last Updated**: 2025-12-19

This document tracks all bugs, refactoring work, and TODO items in one place.

---

## 🐛 Known Bugs

### Active Bugs

None currently! 🎉

### Recently Fixed Bugs

#### 1. Canonical Name Format Mismatch (Fixed 2025-12-19)
- **Issue**: Identity scanner stored `artist::token`, organizer looked for `artist:path:token`
- **Impact**: Canonical mappings never applied, duplicate artist directories
- **Fix**: [organizer.py:324-330](audio_meta/organizer.py#L324-L330) - Added fallback format check
- **Status**: ✅ Fixed, awaiting user re-scan

#### 2. Comma-Separated Artists Mis-Organized (Fixed 2025-12-19)
- **Issue**: "Composer, Ensemble" always used first artist (composer)
- **Impact**: Classical music organized under wrong artist
- **Fix**: [organizer.py:261-293](audio_meta/organizer.py#L261-L293) - Smart ensemble detection
- **Status**: ✅ Fixed, awaiting user re-scan

#### 3. Name Variants Not Unified (Fixed 2025-12-19)
- **Issue**: "Beethoven" vs "Ludwig van Beethoven" created separate directories
- **Impact**: Duplicate directories for name variants
- **Fix**: [identity.py:374-447](audio_meta/identity.py) - Substring cluster merging
- **Status**: ✅ Fixed, awaiting user re-scan

---

## 🔄 Refactoring - Clean Architecture Migration

### Current Phase: Phase 1 - Identity & Matching ⏳

**Goal**: Extract identity/canonicalization into clean core layer with NO I/O dependencies

**Progress**: 30% Complete (4/14 tasks done)

#### ✅ Completed (Phase 1.1 + 1.4)
- [x] Create `audio_meta/core/` directory structure
- [x] Create `audio_meta/core/identity/` package
- [x] Create `audio_meta/core/identity/models.py` - Domain models
- [x] Create `audio_meta/core/identity/matching.py` - Pure matching algorithms with initial matching

#### 🔨 In Progress
- [ ] Extract `IdentityScanner` to `core/identity/scanner.py` (Phase 1.2)
- [ ] Extract `IdentityCanonicalizer` to `core/identity/canonicalizer.py` (Phase 1.3)
- [ ] Integrate `NameMatcher` into scanner (Phase 1.5)
- [ ] Write unit tests (Phase 1.6)

#### 📋 Queued (Phase 1)
- [ ] Update all imports to use new modules
- [ ] Integration testing with real library
- [ ] Update documentation

#### 📋 Future Phases
- **Phase 2**: Organization Logic - Extract `organizer.py` to `core/organization/`
- **Phase 3**: Provider Separation - Isolate MusicBrainz/Discogs into `providers/`
- **Phase 4**: Infrastructure Layer - Move cache/filesystem to `infrastructure/`
- **Phase 5**: Service Cleanup - Make services thin orchestrators
- **Phase 6**: Daemon Refactoring - Extract workflows, get daemon under 1,000 lines
- **Phase 7**: CLI Improvements - Separate CLI from business logic

### Architecture Overview

```
audio_meta/
├── core/                    # Pure business logic (NO I/O) 🆕
│   └── identity/           # Identity domain ✅ Started
│       ├── models.py       # Domain models ✅
│       ├── matching.py     # Matching algorithms ✅
│       ├── scanner.py      # Identity scanning (TODO)
│       └── canonicalizer.py # Canonicalization (TODO)
├── services/               # Application services ✅
│   ├── release_matching.py
│   ├── track_assignment.py
│   ├── classical_music.py
│   └── directory_identity.py
├── providers/              # External APIs (TODO)
├── infrastructure/         # Cache, filesystem (TODO)
├── daemon/                 # Daemon orchestration
└── cli/                    # CLI interface (TODO)
```

---

## ✨ Enhancements

### Recently Added

#### 1. Initial Name Matching (Added 2025-12-19)
- **Purpose**: Catch "J.S. Bach" vs "Johann Sebastian Bach" variants
- **Implementation**: `core/identity/matching.py` - `match_initials()` function
- **Coverage**:
  - ✅ "J.S. Bach" vs "Johann Sebastian Bach"
  - ✅ "W.A. Mozart" vs "Wolfgang Amadeus Mozart"
  - ✅ "L.v. Beethoven" vs "Ludwig van Beethoven"
- **Status**: ✅ Implemented, not yet integrated (needs Phase 1.5)
- **Confidence**: 0.90-0.95 (high confidence)

#### 2. Service Extraction (Completed 2025-12-19)
- Extracted 4 services from daemon (1,229 lines)
- Reduced daemon from 2,189 → 1,803 lines (18% reduction)
- Services: ReleaseMatching, TrackAssignment, ClassicalMusic, DirectoryIdentity

### Planned Enhancements

#### 1. Fuzzy String Matching (Future)
- **Purpose**: Catch typos and minor variations
- **Method**: Levenshtein distance with configurable threshold
- **Priority**: Low (initial matching covers most cases)
- **Location**: `core/identity/matching.py` - add `match_fuzzy()`

#### 2. Manual Override System (Future)
- **Purpose**: Let users manually specify canonical names
- **Implementation**: Config file or CLI command
- **Priority**: Medium
- **Use Case**: Handle edge cases that algorithms miss

---

## 📋 TODO Lists

### High Priority (This Week)

1. **Continue Phase 1 Refactoring**
   - [ ] Extract `IdentityScanner` to core layer
   - [ ] Extract `IdentityCanonicalizer` to core layer
   - [ ] Integrate `NameMatcher` into scanner
   - [ ] Write unit tests for matching module

2. **Test Bug Fixes**
   - [ ] User runs `python -m audio_meta scan` to test fixes
   - [ ] Verify "Art Blakey" variants unified
   - [ ] Verify "Beethoven" variants unified
   - [ ] Verify classical ensembles organized correctly

### Medium Priority (Next Week)

3. **Phase 2: Organization Logic**
   - [ ] Create `core/organization/` package
   - [ ] Extract path building logic
   - [ ] Extract organization rules
   - [ ] Write tests

4. **Documentation**
   - [ ] Add docstrings to all core modules
   - [ ] Create API documentation
   - [ ] Add architecture diagrams

### Low Priority (Future)

5. **Testing Infrastructure**
   - [ ] Set up pytest configuration
   - [ ] Create test fixtures for common scenarios
   - [ ] Add CI/CD pipeline

6. **Performance Optimization**
   - [ ] Profile identity scanning
   - [ ] Optimize token normalization
   - [ ] Cache frequently-used matches

---

## 📊 Metrics

### Code Size Evolution

| Component | Before Refactoring | After Services | After Core | Target |
|-----------|-------------------|----------------|------------|--------|
| Daemon Core | 2,189 lines | 1,803 lines | 1,803 lines | < 1,000 |
| Services | 0 | 1,229 lines | 1,229 lines | - |
| Core Layer | 0 | 0 | ~400 lines | - |
| **Total** | 2,189 | 3,032 | 3,432 | - |

*Note: Total increases but complexity decreases due to separation of concerns*

### Test Coverage

| Layer | Unit Tests | Integration Tests | Coverage |
|-------|-----------|-------------------|----------|
| Core (Identity) | 0 | 0 | 0% 🔴 |
| Services | 0 | 0 | 0% 🔴 |
| Daemon | 0 | 0 | 0% 🔴 |

**Next Goal**: 100% coverage for `core/identity/matching.py`

### Bug Resolution

- **Total Bugs Fixed**: 3
- **Open Bugs**: 0
- **Average Resolution Time**: < 1 day

---

## 🎯 Current Sprint Goals

### Sprint: Clean Architecture Foundation (Week of 2025-12-19)

**Goal**: Establish core identity layer with pure business logic

**Success Criteria**:
- [ ] Core identity package has zero I/O dependencies
- [ ] NameMatcher has 100% unit test coverage
- [ ] Initial matching catches "J.S. Bach" vs "Johann Sebastian Bach"
- [ ] All existing functionality preserved (backward compatible)

**Deliverables**:
1. ✅ `core/identity/models.py` - Domain models
2. ✅ `core/identity/matching.py` - Matching algorithms
3. 🔨 `core/identity/scanner.py` - Identity scanner (in progress)
4. 🔨 `core/identity/canonicalizer.py` - Canonicalizer (in progress)
5. 🔨 Unit tests for all core modules (in progress)

---

## 📝 Decision Log

### 2025-12-19: Clean Architecture Approach
**Decision**: Adopt clean architecture with gradual migration
**Reasoning**:
- Allows incremental improvement without big bang rewrite
- Maintains backward compatibility
- Improves testability immediately
- Clear separation of concerns

**Alternatives Considered**:
- Big bang rewrite (rejected: too risky)
- Leave as-is (rejected: technical debt growing)

### 2025-12-19: Initial Matching Strategy
**Decision**: Implement initial matching before completing Phase 1
**Reasoning**:
- High value enhancement (catches common classical music edge case)
- Clean implementation in new architecture
- Efficient to build while refactoring
- No dependencies on existing code

---

## 🔗 Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Full architecture specification
- [REFACTORING_TODO.md](REFACTORING_TODO.md) - Detailed refactoring task breakdown (ARCHIVED)
- [BUGFIXES.md](BUGFIXES.md) - Detailed bug fix documentation
- [CANONICAL_EDGE_CASES.md](CANONICAL_EDGE_CASES.md) - Edge case analysis
- [SESSION_SUMMARY.md](SESSION_SUMMARY.md) - Previous session summary (ARCHIVED)
- [PROGRESS.md](PROGRESS.md) - Session progress notes (ARCHIVED)

---

## 📞 Quick Reference

### Running Tests
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/core/identity/test_matching.py

# Run with coverage
pytest --cov=audio_meta
```

### Running Scans
```bash
# Identity pre-scan (builds canonical mappings)
python -m audio_meta scan

# Organize files
python -m audio_meta organize

# Daemon mode
python -m audio_meta daemon
```

### Debugging Identity Issues
```bash
# Check file metadata and canonical mappings
python debug_identity.py check-file "/path/to/file.flac"

# Check cache for a name
python debug_identity.py check-cache "Beethoven"

# List variants
python debug_identity.py list-variants "Art Blakey"
```

---

## 🎨 Coding Standards

### Core Layer Rules
1. **NO I/O**: No file system, no network, no database
2. **Pure Functions**: Prefer pure functions over stateful classes
3. **Type Hints**: All public functions must have type hints
4. **Docstrings**: All public functions must have docstrings
5. **Tests**: 100% coverage for core layer

### General Rules
1. **DRY**: Don't repeat yourself
2. **SOLID**: Follow SOLID principles
3. **Dependencies**: All dependencies point inward (toward core)
4. **Backward Compatible**: Maintain compatibility during migration
5. **Document Changes**: Update this file when making changes

---

*This is the single source of truth for development tracking. Update this file instead of creating separate TODO/BUGS/PROGRESS files.*
