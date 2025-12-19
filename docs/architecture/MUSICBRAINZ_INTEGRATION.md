# MusicBrainz Integration for Identity Resolution

## What You Already Have ✅

**Good news**: You already have MusicBrainz integrated!

### Existing Integration
- **Library**: `musicbrainzngs` already installed and imported
- **Provider**: `audio_meta/providers/musicbrainz.py` (500+ lines)
- **Usage**: Currently used for:
  - Release matching (album identification)
  - Track metadata enrichment
  - AcoustID fingerprint matching

### Available API Methods
```python
import musicbrainzngs

# Already available in your codebase:
musicbrainzngs.search_artists(query="Miles Davis")
musicbrainzngs.get_artist_by_id(artist_id, includes=['aliases'])
musicbrainzngs.search_recordings(artist="Miles Davis", recording="So What")
```

---

## What We Need to Add 🔨

To use MusicBrainz for identity resolution, we need to create a NEW service that:

### 1. Artist Search & Resolution
Query MusicBrainz for artist information and aliases.

### 2. Local Caching
Store results to avoid repeated API calls (rate limited to 1/second).

### 3. Integration with IdentityScanner
Use MusicBrainz data during identity clustering.

---

## Implementation Plan

### Step 1: Create MusicBrainz Identity Resolver

```python
# audio_meta/providers/musicbrainz/identity_resolver.py

from dataclasses import dataclass
from typing import Optional
import musicbrainzngs
import logging

logger = logging.getLogger(__name__)


@dataclass
class ArtistIdentity:
    """MusicBrainz artist identity with aliases."""
    mb_id: str  # MusicBrainz ID
    canonical_name: str  # Primary name
    aliases: list[str]  # All known aliases
    artist_type: str  # "Person", "Group", "Orchestra", etc.
    sort_name: Optional[str] = None
    disambiguation: Optional[str] = None


class MusicBrainzIdentityResolver:
    """
    Resolve artist identities using MusicBrainz.

    Rate limit: 1 request per second (enforced by musicbrainzngs)
    """

    def __init__(self, cache=None):
        self.cache = cache
        # Initialize MusicBrainz client
        musicbrainzngs.set_useragent(
            "audio-meta",
            "1.0",
            "https://github.com/yourusername/audio-meta"
        )

    def search_artist(self, name: str) -> Optional[ArtistIdentity]:
        """
        Search MusicBrainz for an artist and return identity.

        Args:
            name: Artist name to search

        Returns:
            ArtistIdentity if found, None otherwise
        """
        # Check cache first
        if self.cache:
            cached = self.cache.get_artist_identity(name)
            if cached:
                return cached

        try:
            # Search MusicBrainz
            result = musicbrainzngs.search_artists(
                artist=name,
                limit=5  # Get top 5 matches
            )

            if not result.get('artist-list'):
                return None

            # Take best match (first result)
            artist_data = result['artist-list'][0]

            # Get full artist details with aliases
            artist_id = artist_data['id']
            full_data = musicbrainzngs.get_artist_by_id(
                artist_id,
                includes=['aliases']
            )

            artist = full_data['artist']

            # Extract aliases
            aliases = [artist['name']]  # Include primary name
            if 'alias-list' in artist:
                for alias in artist['alias-list']:
                    aliases.append(alias['alias'])

            identity = ArtistIdentity(
                mb_id=artist['id'],
                canonical_name=artist['name'],
                aliases=aliases,
                artist_type=artist.get('type', 'Unknown'),
                sort_name=artist.get('sort-name'),
                disambiguation=artist.get('disambiguation')
            )

            # Cache result
            if self.cache:
                self.cache.store_artist_identity(name, identity)

            return identity

        except Exception as exc:
            logger.warning(f"MusicBrainz lookup failed for '{name}': {exc}")
            return None

    def resolve_variants(self, names: list[str]) -> dict[str, ArtistIdentity]:
        """
        Resolve multiple name variants to canonical identities.

        Args:
            names: List of artist names to resolve

        Returns:
            Dict mapping name to ArtistIdentity
        """
        results = {}

        for name in names:
            identity = self.search_artist(name)
            if identity:
                results[name] = identity

            # Respect rate limit (1 request/second)
            import time
            time.sleep(1.1)

        return results
```

### Step 2: Extend Cache for Artist Identities

```python
# audio_meta/cache.py (add to existing MetadataCache)

def get_artist_identity(self, name: str) -> Optional[dict]:
    """Get cached MusicBrainz artist identity."""
    key = f"mb_artist:{name.lower()}"
    return self.get(key)

def store_artist_identity(self, name: str, identity: ArtistIdentity):
    """Store MusicBrainz artist identity in cache."""
    key = f"mb_artist:{name.lower()}"
    self.set(key, {
        'mb_id': identity.mb_id,
        'canonical_name': identity.canonical_name,
        'aliases': identity.aliases,
        'artist_type': identity.artist_type,
        'sort_name': identity.sort_name,
        'disambiguation': identity.disambiguation
    }, ttl=90 * 24 * 3600)  # Cache for 90 days
```

### Step 3: Integrate with IdentityScanner

```python
# audio_meta/core/identity/scanner.py

def scan_names(
    self,
    names_by_category: dict[str, Iterable[str]],
    use_musicbrainz: bool = False,
    musicbrainz_resolver = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> IdentityScanResult:
    """
    Process name variants into clustered identities.

    Args:
        names_by_category: Dict mapping category to iterable of names
        use_musicbrainz: Whether to use MusicBrainz for resolution
        musicbrainz_resolver: MusicBrainz resolver instance
        progress_callback: Optional callback(category, count)

    Returns:
        IdentityScanResult with clustered identities
    """
    result = IdentityScanResult()

    for category, names in names_by_category.items():
        # ... existing clustering logic ...

        # NEW: MusicBrainz resolution (optional)
        if use_musicbrainz and musicbrainz_resolver and category == "artist":
            # Get all unique names
            unique_names = set()
            for cluster in clusters.values():
                unique_names.update(cluster.variants)

            # Resolve with MusicBrainz
            mb_identities = musicbrainz_resolver.resolve_variants(list(unique_names))

            # Merge clusters based on MusicBrainz results
            clusters = self._merge_musicbrainz_clusters(clusters, mb_identities)

        # Store in result
        # ...
```

---

## What It Involves

### Time Estimate
- **Code Implementation**: 4-6 hours
- **Testing**: 2-3 hours
- **Documentation**: 1 hour
- **Total**: ~1 day of work

### Effort Breakdown

#### Easy Parts (Already Done) ✅
1. MusicBrainz library already installed
2. Network retry logic already in `musicbrainz.py`
3. Cache infrastructure already exists
4. Rate limiting handled by `musicbrainzngs`

#### New Code Needed 🔨
1. **MusicBrainzIdentityResolver** (~150 lines)
   - Artist search method
   - Alias extraction
   - Result parsing

2. **Cache Extensions** (~50 lines)
   - Artist identity storage
   - TTL management

3. **Scanner Integration** (~100 lines)
   - Optional MusicBrainz resolution
   - Cluster merging based on MB data

#### Configuration 📝
```yaml
# config.yaml
identity:
  use_musicbrainz: false  # Default: off (requires network)
  musicbrainz_cache_days: 90  # Cache results for 90 days
  musicbrainz_batch_size: 50  # Resolve in batches
```

---

## Performance Implications

### Rate Limits ⏱️
- **MusicBrainz**: 1 request per second
- **For 1000 artists**: ~17 minutes initial scan
- **Cached after first scan**: Instant lookups for 90 days

### Network Dependency 🌐
- **Requires internet** during identity pre-scan
- **Offline mode**: Falls back to local algorithms
- **Cache persists**: Works offline after initial scan

### Optimization Strategies

#### 1. Batch Processing
```python
# Only query for unmatched names after local matching
local_matches = self.matcher.merge_clusters(...)
unmatched = [c for c in local_matches if c.confidence < 0.85]
# Only query MusicBrainz for these
```

#### 2. Incremental Updates
```python
# Only scan new/changed artists
last_scan = cache.get_last_identity_scan()
new_artists = [a for a in all_artists if a.modified > last_scan]
```

#### 3. Background Processing
```python
# Run MusicBrainz resolution in background
# User can continue organizing while it runs
import threading
thread = threading.Thread(target=resolve_artists)
thread.start()
```

---

## Example Usage

### Scenario 1: First-Time Scan (With MusicBrainz)
```bash
# Run identity pre-scan with MusicBrainz
audio-meta scan --use-musicbrainz

# Takes ~17 minutes for 1000 artists (rate limited)
# Results cached for 90 days
```

**Output**:
```
Scanning library for identities...
├─ Found 1500 artist variants
├─ Local matching: 1200 variants → 450 clusters
├─ MusicBrainz resolution: 450 queries
│  ├─ "Art Blakey & The Jazz Messengers" → MB:5bfb0b0...
│  │  Aliases: Art Blakey and The Jazz Messengers, The Jazz Messengers
│  ├─ "J.S. Bach" → MB:24f1766e...
│  │  Canonical: Johann Sebastian Bach
│  │  Aliases: JS Bach, J.S. Bach, Bach
│  └─ ...
└─ Final: 420 unique artists (30 merged via MusicBrainz)

Results cached for 90 days.
```

### Scenario 2: Subsequent Scans (Cached)
```bash
# Re-scan with cached MusicBrainz data
audio-meta scan

# Instant! Uses cached results
```

### Scenario 3: Offline Mode
```bash
# Scan without network
audio-meta scan --no-musicbrainz

# Uses local algorithms only
```

---

## Benefits vs Costs

### Benefits ✅
1. **Authoritative Data**: MusicBrainz is the gold standard
2. **Handles Band Names**: Knows "Art Blakey & The Jazz Messengers" is one entity
3. **Aliases**: Gets all known variants automatically
4. **Artist Types**: Distinguishes Person vs Group vs Orchestra
5. **Disambiguation**: Handles artists with same name

### Costs ❌
1. **Network Required**: Initial scan needs internet
2. **Time**: ~17 minutes for 1000 artists (first time only)
3. **Dependency**: Requires working MusicBrainz service
4. **Complexity**: More code to maintain

---

## Recommendation

### Start Simple, Add Later

**Phase 1 (Now)**: Implement local fuzzy matching
- No network dependency
- Instant
- Covers 95% of cases
- ~4 hours work

**Phase 2 (Later)**: Add MusicBrainz as optional enhancement
- User opts in via config
- Runs in background
- Enhances local matching
- ~1 day work

### Implementation Order

1. ✅ **Fuzzy matching** (Priority 1)
   - Uses built-in `difflib`
   - No dependencies
   - Fast

2. ✅ **Manual overrides** (Priority 1)
   - Simple YAML config
   - User control
   - Instant

3. 🔜 **MusicBrainz** (Priority 2)
   - Optional feature
   - Background processing
   - Cached results

---

## Code Snippet: Quick MusicBrainz Test

Want to try it right now? Here's a quick test:

```python
#!/usr/bin/env python3
"""Test MusicBrainz artist lookup."""
import musicbrainzngs

# Set user agent (required by MusicBrainz)
musicbrainzngs.set_useragent("audio-meta-test", "1.0", "https://github.com/test")

def test_artist_lookup(name: str):
    print(f"\nSearching for: {name}")

    # Search
    result = musicbrainzngs.search_artists(artist=name, limit=3)

    if not result.get('artist-list'):
        print("  No results")
        return

    # Get first match
    artist = result['artist-list'][0]
    artist_id = artist['id']

    print(f"  Found: {artist['name']} (type: {artist.get('type', 'unknown')})")
    print(f"  MB ID: {artist_id}")
    print(f"  Score: {artist.get('ext:score', 'N/A')}")

    # Get full details with aliases
    full = musicbrainzngs.get_artist_by_id(artist_id, includes=['aliases'])
    artist_data = full['artist']

    if 'alias-list' in artist_data:
        print(f"  Aliases:")
        for alias in artist_data['alias-list'][:5]:  # First 5
            print(f"    - {alias['alias']}")

# Test cases
test_artist_lookup("Art Blakey & The Jazz Messengers")
test_artist_lookup("J.S. Bach")
test_artist_lookup("Miles Davis")
test_artist_lookup("Beethoven")
```

Run it:
```bash
python test_musicbrainz_identity.py
```

---

## Decision Time

**Which approach do you prefer?**

**Option A**: Start with fuzzy matching + manual overrides (quick, simple)
- 4 hours of work
- No network dependency
- Covers 95% of cases
- Can add MusicBrainz later

**Option B**: Build MusicBrainz integration now
- 1 day of work
- Network required for initial scan
- Most comprehensive solution
- Cached results work offline

**Option C**: Do both (recommended hybrid)
- Week 1: Fuzzy + overrides (quick win)
- Week 2: MusicBrainz (optional enhancement)
- Best of both worlds

My recommendation: **Option A first**, then add MusicBrainz as an optional enhancement if needed.

What do you think?
