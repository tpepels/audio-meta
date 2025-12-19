# Audio Meta Documentation

This directory contains all project documentation organized by topic.

---

## 📋 Main Documents (Project Root)

### [DEVELOPMENT.md](../DEVELOPMENT.md) 🌟
**The single source of truth for development tracking**
- Current bugs and fixes
- Refactoring progress and TODO lists
- Enhancements and features
- Metrics and sprint goals
- Decision log

**Read this first!** All active development information is here.

### [README.md](../README.md)
User-facing documentation for the audio-meta tool
- Installation instructions
- Usage examples
- Configuration guide

---

## 📁 Documentation Sections

### Architecture (`architecture/`)
Technical architecture and design documents

- **[ARCHITECTURE.md](architecture/ARCHITECTURE.md)** - Clean architecture specification
  - Layer definitions and responsibilities
  - Dependency rules
  - Migration strategy
  - Directory structure

### Bugs & Fixes (`bugs/`)
Bug reports and fix documentation

- **[BUGFIXES.md](bugs/BUGFIXES.md)** - Detailed bug fix documentation
  - Root cause analysis
  - Implementation details
  - Testing instructions

### Reference (`reference/`)
Reference documentation and analysis

- **[CANONICAL_NAME_ANALYSIS.md](reference/CANONICAL_NAME_ANALYSIS.md)** - Step-by-step canonical name system flow
- **[CANONICAL_EDGE_CASES.md](reference/CANONICAL_EDGE_CASES.md)** - Edge case analysis and coverage

### Archive (`archive/`)
Historical documents (for reference only, may be outdated)

- `REFACTORING_TODO.md` - Original detailed refactoring plan (replaced by DEVELOPMENT.md)
- `SESSION_SUMMARY.md` - Previous session summary
- `PROGRESS.md` - Old progress tracking (replaced by DEVELOPMENT.md)
- `REFACTORING_NOTES.md` - Service extraction notes
- `TODO.md` - Old TODO list (replaced by DEVELOPMENT.md)

---

## 🎯 Quick Links by Task

### I want to...

**Understand the codebase architecture**
→ Read [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)

**See what's being worked on**
→ Read [DEVELOPMENT.md](../DEVELOPMENT.md) § Current Sprint Goals

**Track refactoring progress**
→ Read [DEVELOPMENT.md](../DEVELOPMENT.md) § Refactoring - Clean Architecture Migration

**Understand bug fixes**
→ Read [bugs/BUGFIXES.md](bugs/BUGFIXES.md)

**See known issues**
→ Read [DEVELOPMENT.md](../DEVELOPMENT.md) § Known Bugs

**Understand canonical name system**
→ Read [reference/CANONICAL_NAME_ANALYSIS.md](reference/CANONICAL_NAME_ANALYSIS.md)

**See what edge cases are handled**
→ Read [reference/CANONICAL_EDGE_CASES.md](reference/CANONICAL_EDGE_CASES.md)

---

## 📝 Documentation Guidelines

### When to Update

**Update DEVELOPMENT.md when**:
- Adding/fixing bugs
- Completing refactoring tasks
- Adding enhancements
- Making architectural decisions
- Updating sprint goals

**Create new docs when**:
- Documenting complex systems (like canonical names)
- Writing architecture specs
- Documenting major features

**Don't create**:
- Separate TODO lists (use DEVELOPMENT.md)
- Separate bug trackers (use DEVELOPMENT.md)
- Progress notes (use DEVELOPMENT.md)

### Document Organization

```
docs/
├── README.md              ← You are here
├── architecture/          ← Design & architecture
├── bugs/                  ← Bug reports & fixes
├── reference/             ← Reference material
└── archive/              ← Historical docs
```

---

## 🔄 Document Lifecycle

1. **Active** - Lives in project root or relevant `docs/` subdirectory
2. **Current** - Referenced in DEVELOPMENT.md
3. **Archived** - Moved to `docs/archive/` when superseded

**Active Documents**:
- `/DEVELOPMENT.md` - Main development tracker
- `/README.md` - User documentation
- `docs/architecture/ARCHITECTURE.md` - Architecture spec
- `docs/bugs/BUGFIXES.md` - Bug fix documentation
- `docs/reference/*.md` - Reference material

**Archived Documents** (in `archive/`):
- Historical tracking documents
- Old TODO lists
- Session summaries
- Outdated architecture notes

---

*Keep documentation organized, up-to-date, and easy to navigate!*
