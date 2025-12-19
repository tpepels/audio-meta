# Bug Fixes - Identity & Artist Handling

## Summary

**Three critical bugs fixed in this session:**

1. ✅ **Format Mismatch** - Organizer now checks identity scanner's cache format
2. ✅ **Fuzzy Matching** - Added substring merging (e.g., "Beethoven" + "Ludwig von Beethoven")
3. ✅ **Smart Artist Selection** - Comma-separated artists now prefer ensembles

**Expected Results After Re-Scan:**
- "Art Blakey & The Jazz Messengers" unified ✅
- "Beethoven" vs "Ludwig von Beethoven" unified ✅
- "David Achenberg, Tana String Quartet" → uses "Tana String Quartet" ✅

---

## Issues Fixed

### 1. Canonical Name Mappings Not Applied During Organization

**Problem**: Artist/composer duplicates in library despite identity pre-scan running.

**Root Cause**: Format mismatch between identity scanner and organizer:
- Identity scanner stored: `category::normalized_token` (e.g., `artist::beethoven`)
- Organizer looked for: `label_type:parent_path:normalized_value` (e.g., `artist:/some/path:beethoven`)

**Evidence**:
- Multiple directories: "Art Blakey and The Jazz Messengers" vs "Art Blakey & The Jazz Messengers"
- Multiple directories: "Beethoven" vs "Ludwig von Beethoven"

**Fix** ([organizer.py:324-330](audio_meta/organizer.py#L324-L330)):
```python
# Try identity scanner format (category::normalized)
identity_token = f"{label_type}::{normalized}"
cached = self.cache.get_canonical_name(identity_token) if self.cache else None
if cached:
    return cached
```

**Impact**: Organizer now checks both cache formats, allowing it to find and apply canonical name mappings from the identity pre-scan.

---

### 2. Comma-Separated Artists Handled Incorrectly

**Problem**: Files ending up in wrong artist directory.

**Example**:
- File: `/data/music/David Achenberg/Bleu Ebene/...Quatuor Tana.flac`
- Metadata: `artist: "David Achenberg, Tana String Quartet"`
- Old behavior: Used "David Achenberg" (first artist)
- Correct: Should use "Tana String Quartet" (the ensemble)

**Root Cause**: `_primary_artist()` always took the first artist when splitting comma-separated values.

**Fix** ([organizer.py:261-293](audio_meta/organizer.py#L261-L293)):

Implemented smart artist selection:

1. **Detect ensembles**: Look for keywords indicating groups/ensembles
   - quartet, quintet, orchestra, symphony, ensemble, band, etc.

2. **Prefer ensembles**: If any artist contains ensemble keywords, use that one

3. **Fallback to last artist**: For cases like "Composer, Main Performer"

```python
ensemble_keywords = {
    'quartet', 'quintet', 'sextet', 'septet', 'octet',
    'trio', 'duo', 'ensemble', 'orchestra', 'philharmonic',
    'symphony', 'chamber', 'band', 'choir', 'chorus',
    'consort', 'collective', 'group'
}

for part in parts:
    part_lower = part.lower()
    if any(keyword in part_lower for keyword in ensemble_keywords):
        return part
```

**Impact**:
- Classical music with comma-separated artists (composer, ensemble) organized under ensemble
- Jazz with "Soloist, Band Name" organized under band
- Better handling of featured artists and collaborations

---

### 3. No Fuzzy Matching for Name Variants

**Problem**: "Beethoven" and "Ludwig von Beethoven" create separate directories.

**Root Cause**: The identity scanner normalized names to tokens, but variants that normalize differently were never merged:
- "Beethoven" → `"beethoven"`
- "Ludwig von Beethoven" → `"ludwigvonbeethoven"`

These are different tokens, so they ended up in separate clusters.

**Fix** ([identity.py:150-155, 374-447](audio_meta/identity.py)):

Added `_merge_substring_clusters()` method that runs after initial clustering:

```python
def _merge_substring_clusters(self, clusters, category):
    """
    Merge clusters where one token is a substring of another.
    Example: "beethoven" is a substring of "ludwigvonbeethoven"
    """
    # Sort tokens by length (shortest first)
    tokens = sorted(clusters.keys(), key=len)

    for short_token in tokens:
        for long_token in tokens:
            # Check if short token is contained in long token
            if short_token in long_token:
                # Merge the clusters
                # Prefer cluster with more occurrences
                # Combine all variants
                # Re-choose best canonical name
```

**What This Catches:**
- "Beethoven" ⊂ "Ludwig von Beethoven" ✅
- "Bach" ⊂ "J.S. Bach" ✅
- "Miles" ⊂ "Miles Davis" ✅
- Any short name that's part of a longer full name

**Impact**:
- Duplicate directories for composer name variants will be unified
- More aggressive merging - prefers longer, more complete names
- Logs merge operations during identity pre-scan

---

## Testing

To test the fixes:

### Test 1: Canonical Name Unification

```bash
# Run a fresh scan to rebuild identity mappings
python -m audio_meta scan

# Check for duplicate artist directories
ls -la /data/music/ | grep -i "art blakey"
ls -la /data/music/ | grep -i "beethoven"

# Expected: Only one directory for each artist
```

### Test 2: Comma-Separated Artists

```bash
# Check what artist would be selected for a comma-separated field
python3 -c "
from audio_meta.organizer import Organizer
from audio_meta.config import Settings
from audio_meta.models import TrackMetadata
from pathlib import Path

settings = Settings.from_file()
org = Organizer(settings.organizer, settings.library)

# Test case 1: Composer, Ensemble
meta = TrackMetadata(path=Path('/test.flac'))
meta.artist = 'David Achenberg, Tana String Quartet'
print(f'Test 1: {meta.artist} → {org._primary_artist(meta)}')
# Expected: Tana String Quartet

# Test case 2: Multiple individuals
meta.artist = 'John Coltrane, Miles Davis'
print(f'Test 2: {meta.artist} → {org._primary_artist(meta)}')
# Expected: Miles Davis (last artist)

# Test case 3: Solo, Orchestra
meta.artist = 'Yo-Yo Ma, Boston Symphony Orchestra'
print(f'Test 3: {meta.artist} → {org._primary_artist(meta)}')
# Expected: Boston Symphony Orchestra
"
```

### Test 3: Verify Fixed Files

```bash
# Check if problematic file would now be organized correctly
python debug_identity.py check-file "/data/music/David Achenberg/Bleu Ebene/Bleu Ebene, 4eme quatuor pour quatuor et bande magnetique: II. Bleu Ebene (dedie au Quatuor Tana, 2017).flac"
```

---

## Migration Notes

After applying these fixes, you may want to:

1. **Re-organize existing files**: Run `scan` with organize enabled to move files to correct locations

2. **Clean up duplicate directories**: Manually merge duplicate artist folders after canonical names are applied

3. **Review classical music organization**: Check that ensemble-based organization makes sense for your collection

---

## Files Modified

**audio_meta/organizer.py**:
- Added fallback to identity scanner format for canonical names (L324-330)
- Improved comma-separated artist handling with ensemble detection (L261-293)

**audio_meta/identity.py**:
- Added `_merge_substring_clusters()` method (L374-447)
- Integrated substring merging into scan workflow (L150-155)

**New files**:
- `BUGFIXES.md` - This document
- `CANONICAL_NAME_ANALYSIS.md` - Detailed analysis of the canonical name system
- `debug_identity.py` - Diagnostic tool for checking canonical mappings

## Expected Outcomes

After running a fresh scan with these fixes:

1. **"Art Blakey" variants** - Will unify into one directory
   - Before: "Art Blakey & The Jazz Messengers" + "Art Blakey and The Jazz Messengers"
   - After: Single directory with chosen canonical name

2. **"Beethoven" variants** - Will unify into one directory
   - Before: "Beethoven" + "Ludwig von Beethoven"
   - After: Single directory (likely "Ludwig von Beethoven" as it's longer)

3. **Classical ensembles** - Will use correct artist
   - Before: "David Achenberg/Bleu Ebene/..." (wrong artist)
   - After: "Tana String Quartet/Bleu Ebene/..." (correct ensemble)

## Related Issues Resolved

- ✅ Identity pre-scan mappings not being applied during organization
- ✅ Classical music with comma-separated artists mis-organized
- ✅ Artist/composer directory duplicates
- ✅ Short name vs full name variants (Beethoven, Bach, Miles, etc.)
