# Audio Meta - Target Architecture

## Overview

This document defines the target architecture for the audio-meta project, following clean architecture principles with clear separation of concerns.

---

## Directory Structure

```
audio_meta/
├── core/                          # Core business logic (domain layer)
│   ├── __init__.py
│   ├── models.py                  # Domain models (TrackMetadata, etc.)
│   ├── identity/                  # Identity & canonicalization domain
│   │   ├── __init__.py
│   │   ├── scanner.py             # Identity scanning logic
│   │   ├── canonicalizer.py      # Canonical name application
│   │   ├── matching.py            # Name matching algorithms
│   │   └── models.py              # Identity domain models
│   ├── organization/              # File organization domain
│   │   ├── __init__.py
│   │   ├── organizer.py           # Organization logic
│   │   ├── path_builder.py       # Path construction
│   │   └── models.py              # Organization models
│   └── metadata/                  # Metadata extraction domain
│       ├── __init__.py
│       ├── extractor.py           # Metadata extraction
│       └── enrichment.py          # Metadata enrichment
│
├── services/                      # Application services (use cases)
│   ├── __init__.py
│   ├── daemon_facade.py           # Legacy daemon facade
│   ├── release_matching.py        # Release matching service
│   ├── track_assignment.py        # Track assignment service
│   ├── classical_music.py         # Classical music service
│   └── directory_identity.py      # Directory identity service
│
├── providers/                     # External data providers
│   ├── __init__.py
│   ├── musicbrainz/               # MusicBrainz integration
│   │   ├── __init__.py
│   │   ├── client.py              # API client
│   │   ├── mapper.py              # Data mapping
│   │   └── models.py              # Provider-specific models
│   ├── discogs/                   # Discogs integration
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── mapper.py
│   │   └── models.py
│   └── acoustid/                  # AcoustID integration
│       ├── __init__.py
│       ├── client.py
│       └── models.py
│
├── infrastructure/                # Infrastructure & persistence
│   ├── __init__.py
│   ├── cache/                     # Caching layer
│   │   ├── __init__.py
│   │   ├── metadata_cache.py     # Metadata caching
│   │   └── backends/              # Cache backend implementations
│   │       ├── __init__.py
│   │       ├── sqlite.py
│   │       └── memory.py
│   ├── filesystem/                # Filesystem operations
│   │   ├── __init__.py
│   │   ├── scanner.py             # File scanning
│   │   └── operations.py          # File operations
│   └── config/                    # Configuration
│       ├── __init__.py
│       ├── settings.py            # Settings management
│       └── models.py              # Config models
│
├── daemon/                        # Daemon mode (workflow orchestration)
│   ├── __init__.py
│   ├── core.py                    # Main daemon orchestrator
│   ├── workflows/                 # Workflow implementations
│   │   ├── __init__.py
│   │   ├── directory_workflow.py # Directory processing
│   │   └── track_workflow.py     # Track processing
│   └── types.py                   # Daemon-specific types
│
├── cli/                           # Command-line interface
│   ├── __init__.py
│   ├── main.py                    # CLI entry point
│   ├── commands/                  # CLI commands
│   │   ├── __init__.py
│   │   ├── scan.py               # Scan command
│   │   ├── organize.py           # Organize command
│   │   └── daemon.py             # Daemon command
│   └── ui/                        # CLI UI components
│       ├── __init__.py
│       ├── progress.py           # Progress display
│       └── prompts.py            # User prompts
│
└── utils/                         # Shared utilities
    ├── __init__.py
    ├── text.py                    # Text processing utilities
    ├── logging.py                 # Logging utilities
    └── validation.py              # Validation utilities
```

---

## Layer Responsibilities

### 1. Core (Domain Layer)
**Purpose**: Pure business logic, no dependencies on external systems

**Characteristics**:
- No I/O operations (no file system, no network)
- No framework dependencies
- Fully testable with unit tests
- Contains domain models and business rules

**Examples**:
- `core/identity/matching.py` - Name matching algorithms (substring, initials)
- `core/identity/scanner.py` - Identity clustering logic
- `core/organization/path_builder.py` - Path construction rules

### 2. Services (Application Layer)
**Purpose**: Use cases and application workflows

**Characteristics**:
- Orchestrates core domain logic
- Can depend on core and infrastructure
- Implements specific use cases
- Stateless when possible

**Examples**:
- `services/release_matching.py` - Release matching workflow
- `services/track_assignment.py` - Track assignment workflow

### 3. Providers (Integration Layer)
**Purpose**: External API integrations

**Characteristics**:
- Adapts external APIs to internal models
- Handles API-specific logic
- Can be mocked/stubbed for testing
- Isolated from core business logic

**Examples**:
- `providers/musicbrainz/client.py` - MusicBrainz API client
- `providers/discogs/mapper.py` - Maps Discogs data to domain models

### 4. Infrastructure (Infrastructure Layer)
**Purpose**: Technical concerns (caching, file system, config)

**Characteristics**:
- Implementation details
- Swappable implementations (e.g., different cache backends)
- No business logic

**Examples**:
- `infrastructure/cache/metadata_cache.py` - Metadata caching
- `infrastructure/filesystem/scanner.py` - File system scanning

### 5. Daemon (Presentation Layer)
**Purpose**: Workflow orchestration for daemon mode

**Characteristics**:
- Coordinates services and providers
- Manages workflow state
- Handles user interaction in daemon mode

### 6. CLI (Presentation Layer)
**Purpose**: Command-line interface

**Characteristics**:
- User interaction
- Delegates to services
- No business logic

---

## Dependency Rules

```
CLI/Daemon → Services → Core
                ↓         ↑
            Providers     |
                ↓         |
          Infrastructure  |
                └─────────┘
```

**Key Rules**:
1. **Core** has NO dependencies (except standard library)
2. **Services** can depend on Core and Infrastructure
3. **Providers** can depend on Core (for models)
4. **Infrastructure** can depend on Core (for models)
5. **CLI/Daemon** can depend on everything
6. Dependencies only point INWARD (toward Core)

---

## Migration Strategy

### Phase 1: Identity & Matching (Current Priority) ✅

**Goal**: Extract identity/canonicalization into clean architecture

**Tasks**:
1. Create `core/identity/` package
2. Move identity scanning logic from `identity.py` → `core/identity/scanner.py`
3. Move canonicalization logic → `core/identity/canonicalizer.py`
4. Create `core/identity/matching.py` for matching algorithms
5. Add initial matching enhancement (fix for "J.S. Bach" vs "Johann Sebastian Bach")

**Success Criteria**:
- Identity logic is in `core/identity/` with no external dependencies
- All matching logic is testable in isolation
- Original functionality preserved

### Phase 2: Organization Logic

**Goal**: Extract organization logic into clean architecture

**Tasks**:
1. Create `core/organization/` package
2. Move organizer logic from `organizer.py` → `core/organization/organizer.py`
3. Move path building → `core/organization/path_builder.py`
4. Separate file operations into infrastructure layer

### Phase 3: Provider Separation

**Goal**: Isolate external API integrations

**Tasks**:
1. Create `providers/musicbrainz/` package
2. Create `providers/discogs/` package
3. Move API clients and mappers
4. Create clean interfaces

### Phase 4: Infrastructure Layer

**Goal**: Separate technical concerns

**Tasks**:
1. Create `infrastructure/cache/` package
2. Create `infrastructure/filesystem/` package
3. Move cache implementation
4. Move file system operations

### Phase 5: Service Cleanup

**Goal**: Ensure services are pure application logic

**Tasks**:
1. Refactor services to use core layer
2. Remove business logic from services
3. Make services thin orchestrators

---

## Testing Strategy

### Unit Tests (Fast, No I/O)
- **Target**: Core layer
- **Focus**: Business logic, algorithms
- **Examples**:
  - Name matching algorithms
  - Path construction rules
  - Token normalization

### Integration Tests (With Mocks)
- **Target**: Services layer
- **Focus**: Workflow orchestration
- **Examples**:
  - Release matching with mocked providers
  - Track assignment with mocked cache

### End-to-End Tests (Real Files)
- **Target**: CLI/Daemon
- **Focus**: Full workflows
- **Examples**:
  - Scan directory and organize
  - Identity pre-scan and canonicalization

---

## Current State vs Target

### Current State
```
audio_meta/
├── identity.py (600 lines)        # Mixed: domain + infrastructure
├── organizer.py (800 lines)       # Mixed: domain + infrastructure
├── daemon/core.py (1,803 lines)   # Mixed: workflow + business logic
├── services/ (4 files)            # Application services ✅
├── cache.py                       # Infrastructure (not separated)
└── models.py                      # Domain models ✅
```

### Target State (After Migration)
```
audio_meta/
├── core/                          # Pure business logic
│   ├── identity/                  # Identity domain
│   ├── organization/              # Organization domain
│   └── metadata/                  # Metadata domain
├── services/                      # Application workflows ✅
├── providers/                     # External integrations
├── infrastructure/                # Technical concerns
├── daemon/                        # Daemon orchestration
└── cli/                          # CLI interface
```

---

## Benefits of Target Architecture

1. **Testability**: Core logic testable without I/O
2. **Maintainability**: Clear separation of concerns
3. **Flexibility**: Easy to swap implementations
4. **Understandability**: Clear dependencies and responsibilities
5. **Extensibility**: Easy to add new providers or services

---

## Notes

- This is a GRADUAL migration, not a big bang rewrite
- Each phase should maintain backward compatibility
- Tests should be added alongside refactoring
- Focus on one layer at a time
