# Canonical Name System - Step-by-Step Analysis

## Overview

The canonical name system has **two separate flows** that need to work together:

1. **Identity Scanner** (during pre-scan) - Builds canonical mappings
2. **Organizer** (during file organization) - Uses canonical mappings

## Step-by-Step Flow

### Phase 1: Identity Pre-Scan (Building Mappings)

#### Step 1.1: Extract Names from Files
```python
# For each audio file, extract artist names
names = {
    "artist": ["Art Blakey & The Jazz Messengers"],
    "album_artist": ["Art Blakey and The Jazz Messengers"],
}
```

#### Step 1.2: Normalize to Tokens
```python
def _normalize_token(value: str) -> str:
    # Remove featuring patterns
    # Unicode normalize (NFKD)
    # Convert to ASCII
    # Lowercase
    # Remove punctuation
    # Remove spaces
    return token

# Examples:
"Art Blakey & The Jazz Messengers" → "artblakeyandthejazzmessengers"
"Art Blakey and The Jazz Messengers" → "artblakeyandthejazzmessengers"
"Beethoven" → "beethoven"
"Ludwig von Beethoven" → "ludwigvonbeethoven"
```

**❌ PROBLEM #1**: "Beethoven" and "Ludwig von Beethoven" create **different tokens**!
- They will be in **separate clusters**
- Won't be unified

#### Step 1.3: Build Clusters
```python
raw_artists = {
    "artblakeyandthejazzmessengers": [
        "Art Blakey & The Jazz Messengers",
        "Art Blakey and The Jazz Messengers"
    ],
    "beethoven": ["Beethoven"],
    "ludwigvonbeethoven": ["Ludwig von Beethoven"]
}

# Each token becomes ONE cluster
clusters = {
    "artblakeyandthejazzmessengers": IdentityCluster(
        canonical="Art Blakey & The Jazz Messengers",  # chosen
        canonical_id="artist::artblakeyandthejazzmessengers",
        variants={"Art Blakey & The Jazz Messengers",
                  "Art Blakey and The Jazz Messengers"}
    ),
    "beethoven": IdentityCluster(
        canonical="Beethoven",
        canonical_id="artist::beethoven",
        variants={"Beethoven"}
    ),
    "ludwigvonbeethoven": IdentityCluster(
        canonical="Ludwig von Beethoven",
        canonical_id="artist::ludwigvonbeethoven",
        variants={"Ludwig von Beethoven"}
    )
}
```

**✅ WORKS**: "Art Blakey & The Jazz Messengers" variants ARE unified (same normalized token)
**❌ FAILS**: "Beethoven" vs "Ludwig von Beethoven" are NOT unified (different tokens)

#### Step 1.4: Store in Cache
```python
# For Art Blakey cluster:
cache.set_canonical_name(
    "artist::artblakeyandthejazzmessengers",  # primary key
    "Art Blakey & The Jazz Messengers"        # canonical value
)

# For each variant:
for variant in ["Art Blakey and The Jazz Messengers"]:
    variant_token = _normalize_token(variant)  # "artblakeyandthejazzmessengers"
    if variant_token != "artblakeyandthejazzmessengers":  # False! Same token
        cache.set_canonical_name(
            f"artist::{variant_token}",
            "Art Blakey & The Jazz Messengers"
        )
# ❌ PROBLEM #2: Variants with same normalized token DON'T get extra entries!
```

### Phase 2: Organization (Using Mappings)

#### Step 2.1: Extract Artist from Metadata
```python
source = meta.album_artist or meta.artist
# e.g., "Art Blakey and The Jazz Messengers"
```

#### Step 2.2: Normalize Value
```python
normalized = self._normalize_token(value)
# "Art Blakey and The Jazz Messengers" → "artblakeyandthejazzmessengers"
```

#### Step 2.3: Build Cache Key (OLD WAY - Path-based)
```python
token = self._canonical_token(label_type, parent, normalized)
# Returns: "artist:/data/music:artblakeyandthejazzmessengers"

cached = cache.get_canonical_name(token)
# ❌ FAILS: No such key in cache!
```

#### Step 2.4: Build Cache Key (NEW WAY - Identity format)
```python
identity_token = f"{label_type}::{normalized}"
# Returns: "artist::artblakeyandthejazzmessengers"

cached = cache.get_canonical_name(identity_token)
# ✅ WORKS: Found! Returns "Art Blakey & The Jazz Messengers"
```

## Test Cases

### Case 1: "Art Blakey" Variants ✅ SHOULD WORK

**Input files have:**
- File 1: `artist: "Art Blakey & The Jazz Messengers"`
- File 2: `artist: "Art Blakey and The Jazz Messengers"`

**Normalized tokens:**
- Both → `"artblakeyandthejazzmessengers"`

**Result:**
- ✅ Same cluster
- ✅ Canonical: "Art Blakey & The Jazz Messengers" (or "...and..." depending on choice logic)
- ✅ Organizer finds it with new fix

**Directory after scan:**
- ✅ ONE directory: "Art Blakey & The Jazz Messengers" (or whichever is canonical)

---

### Case 2: "Beethoven" Variants ❌ WON'T WORK

**Input files have:**
- Directory 1: Files with `composer: "Beethoven"`
- Directory 2: Files with `composer: "Ludwig von Beethoven"`

**Normalized tokens:**
- "Beethoven" → `"beethoven"`
- "Ludwig von Beethoven" → `"ludwigvonbeethoven"`

**Result:**
- ❌ DIFFERENT clusters (different normalized tokens!)
- ❌ No unification happens
- ❌ Organizer will create TWO directories

**Directory after scan:**
- ❌ TWO directories remain:
  - "Beethoven"
  - "Ludwig von Beethoven"

---

### Case 3: Variant with Same Normalized Token ⚠️ PARTIAL

**Input files have:**
- File 1: `artist: "Yo-Yo Ma"`
- File 2: `artist: "Yo Yo Ma"` (no hyphens)

**Normalized tokens:**
- "Yo-Yo Ma" → `"yoyoma"` (hyphens removed)
- "Yo Yo Ma" → `"yoyoma"` (spaces removed)

**Result:**
- ✅ Same cluster (same token)
- ✅ Canonical: One will be chosen
- ⚠️ Cache stores only: `"artist::yoyoma" → "Yo-Yo Ma"`
- ⚠️ NO separate entry for "Yo Yo Ma" variant

**During organization:**
```python
# File with "Yo Yo Ma"
normalized = "yoyoma"
identity_token = "artist::yoyoma"
cached = cache.get_canonical_name("artist::yoyoma")
# ✅ Returns "Yo-Yo Ma"
```

**Result: ✅ WORKS** (variants with same token are handled by primary key)

---

### Case 4: Comma-Separated Artists ⚠️ COMPLEX

**Input file has:**
- `artist: "David Achenberg, Tana String Quartet"`

**What happens in scanner:**
```python
names = _extract_names(file)
# Returns: ["David Achenberg, Tana String Quartet"]  # Whole string!
```

**❌ PROBLEM #3**: The scanner doesn't split comma-separated artists!

**Normalized token:**
- "David Achenberg, Tana String Quartet" → `"davidachenbergtanastringquartet"`

**Result:**
- ✅ Creates cluster with this exact string as a variant
- ❌ But organizer's `_primary_artist()` SPLITS it and uses "Tana String Quartet"
- ❌ Mismatch: Scanner sees full string, organizer uses partial

**Directory after scan:**
- ⚠️ Depends on whether other files have just "Tana String Quartet" alone

---

## Root Causes of Issues

### Issue 1: No Fuzzy Matching
The system relies on **exact normalized token matching**. Names that are semantically the same but normalize differently won't be unified:

- "Beethoven" ≠ "Ludwig von Beethoven"
- "Bach" ≠ "J.S. Bach" ≠ "Johann Sebastian Bach"
- "Miles" ≠ "Miles Davis"

### Issue 2: Comma-Separated Artists Mismatch
- **Scanner** stores: `"David Achenberg, Tana String Quartet"` as one entity
- **Organizer** splits it and uses: `"Tana String Quartet"`
- Cache lookup fails because tokens don't match

### Issue 3: Variant Storage Logic
When variants have the same normalized token, only ONE entry is created:
```python
if variant_token != token:  # Only store if DIFFERENT
    cache.set_canonical_name(f"artist::{variant_token}", canonical)
```

This is actually OK because the primary key covers it, but it means you need to normalize the same way when looking up.

---

## Solutions

### For "Beethoven" vs "Ludwig von Beethoven"

**Option A: Add alias mappings in config**
```yaml
canonical_aliases:
  composer:
    - canonical: "Ludwig van Beethoven"
      aliases: ["Beethoven", "L. v. Beethoven", "Beethoven, Ludwig van"]
```

**Option B: Fuzzy matching with edit distance**
- Detect variants where one is a substring of another
- "Beethoven" is contained in "Ludwig von Beethoven"
- Merge these clusters

**Option C: Manual merge command**
```bash
python -m audio_meta merge-artist "Beethoven" "Ludwig von Beethoven"
```

### For Comma-Separated Artists

**Option A: Split during extraction** (in identity scanner)
```python
def _split_artist_names(value: str) -> list[str]:
    return [p.strip() for p in re.split(r'[;,]', value) if p.strip()]
```

**Option B: Store both full and split versions**
- Store "David Achenberg, Tana String Quartet" as one variant
- Also store "David Achenberg" and "Tana String Quartet" as separate variants
- Link all to the same canonical (ensemble name)

**Option C: Use album_artist more aggressively**
- Many properly-tagged files have `album_artist` separate from `artist`
- Prefer album_artist over artist for organization

---

## Recommended Immediate Fix

The most impactful fix with minimal changes:

1. **Add fuzzy matching for short/long name variants**
   - Detect when one normalized token is a substring of another
   - Merge "beethoven" + "ludwigvonbeethoven" clusters

2. **Split comma-separated values in scanner**
   - Makes scanner behavior match organizer behavior

3. **Add manual merge command**
   - For cases that slip through
