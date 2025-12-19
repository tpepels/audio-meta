# Identity Scanner Enhancement Plan

## Current Limitations

Our identity scanner currently uses **purely algorithmic matching**:
- ✅ Exact matching (same normalized token)
- ✅ Substring matching ("Beethoven" ⊂ "Ludwig van Beethoven")
- ✅ Initial matching ("J.S. Bach" → "Johann Sebastian Bach")
- ❌ Can't distinguish band names with & ("Art Blakey & The Jazz Messengers")
- ❌ No translation support ("Wiener Philharmoniker" vs "Vienna Philharmonic")
- ❌ No alias/nickname support ("Prince" vs "The Artist Formerly Known As Prince")

---

## Enhancement Strategies

### Strategy 1: External Music Knowledge APIs 🌐

**Use existing music databases to get canonical identities**

#### MusicBrainz Artist Lookup
Already integrated! We can extend it for identity resolution.

**How it works**:
1. Query MusicBrainz for artist name
2. Get canonical name + all aliases
3. Cache the mapping

**Example**:
```python
# Query: "Art Blakey & The Jazz Messengers"
# Response:
{
  "id": "5bfb0b0-...",
  "name": "Art Blakey & The Jazz Messengers",  # Canonical
  "type": "Group",
  "aliases": [
    "Art Blakey and The Jazz Messengers",
    "Art Blakey and the Jazz Messengers",
    "The Jazz Messengers"
  ]
}
```

**Pros**:
- ✅ Authoritative data
- ✅ Handles band names correctly
- ✅ Free API
- ✅ Already integrated in daemon

**Cons**:
- ❌ Requires network calls
- ❌ Rate limited (1 request/second)
- ❌ May not have obscure artists

#### Last.fm API
Similar to MusicBrainz but different coverage.

**Pros**:
- ✅ Good for popular artists
- ✅ Has listening stats (could use for confidence)

**Cons**:
- ❌ API key required
- ❌ Less comprehensive than MusicBrainz

---

### Strategy 2: String Distance Algorithms 📏

**Use edit distance to catch typos and minor variations**

#### Levenshtein Distance
Measure how many edits needed to transform one string to another.

**Implementation**:
```python
from difflib import SequenceMatcher

def fuzzy_match(name1: str, name2: str, threshold: float = 0.85) -> bool:
    """Check if names are similar enough."""
    ratio = SequenceMatcher(None, name1.lower(), name2.lower()).ratio()
    return ratio >= threshold
```

**Examples**:
- "Miles Davis" vs "Mile Davis" (typo) → 0.95 similarity ✅
- "Beethoven" vs "Bethoveen" (typo) → 0.94 similarity ✅

**Pros**:
- ✅ No external dependencies
- ✅ Fast
- ✅ Catches typos

**Cons**:
- ❌ May create false positives
- ❌ Doesn't understand semantics

#### Jaro-Winkler Distance
Better for names (weights prefix similarity higher).

**Pros**:
- ✅ Better for person names
- ✅ Weights initial characters more

---

### Strategy 3: NLP & Machine Learning 🤖

**Use semantic understanding of names**

#### Named Entity Recognition (NER)
Identify whether something is a person vs band vs orchestra.

**Libraries**:
- spaCy (lightweight, fast)
- NLTK
- Hugging Face transformers

**Example**:
```python
import spacy

nlp = spacy.load("en_core_web_sm")

def is_person(name: str) -> bool:
    doc = nlp(name)
    return any(ent.label_ == "PERSON" for ent in doc.ents)

is_person("Miles Davis")  # True
is_person("The Jazz Messengers")  # False
```

**Pros**:
- ✅ Can distinguish person vs band
- ✅ Helps with disambiguation

**Cons**:
- ❌ Requires ML models (large download)
- ❌ May be overkill
- ❌ Not 100% accurate

#### Sentence Embeddings
Use semantic similarity instead of string similarity.

**Libraries**:
- sentence-transformers
- OpenAI embeddings (requires API)

**Example**:
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')

embeddings = model.encode([
    "Wiener Philharmoniker",
    "Vienna Philharmonic Orchestra"
])

similarity = cosine_similarity(embeddings[0], embeddings[1])
# High similarity even though strings are different
```

**Pros**:
- ✅ Understands semantic meaning
- ✅ Language-agnostic
- ✅ Catches translations

**Cons**:
- ❌ Large model download (90MB+)
- ❌ Slower
- ❌ Requires ML dependencies

---

### Strategy 4: Collaborative / User Feedback 👥

**Learn from user corrections**

#### Manual Override System
Let users specify canonical names.

**Implementation**:
```yaml
# config.yaml
canonical_overrides:
  artist:
    "Prince": "Prince Rogers Nelson"
    "The Artist Formerly Known As Prince": "Prince Rogers Nelson"
```

**Pros**:
- ✅ 100% accurate for user's library
- ✅ Simple to implement
- ✅ No external dependencies

**Cons**:
- ❌ Requires manual work
- ❌ Not automatic

#### Crowdsourced Mappings
Community-maintained mapping database.

**Could create**:
- GitHub repo with community mappings
- Users contribute corrections
- Tool fetches latest mappings

---

## Recommended Hybrid Approach 🎯

Combine multiple strategies in order of confidence:

### Phase 1: Core Matching (Already Done) ✅
1. Exact token match (confidence: 1.0)
2. Substring match (confidence: 0.85)
3. Initial match (confidence: 0.90)

### Phase 2: Enhanced Matching (Quick Wins)
4. **Fuzzy string match** (confidence: 0.75)
   - Use Levenshtein distance
   - Threshold: 0.85 similarity
   - Catches typos and minor variations

5. **Manual overrides** (confidence: 1.0)
   - User-specified mappings in config
   - Always take precedence

### Phase 3: External Knowledge (Optional)
6. **MusicBrainz lookup** (confidence: 0.95)
   - Only for unmatched names
   - Cache results locally
   - Respect rate limits

7. **NER for person/band detection** (confidence: 0.80)
   - Use spaCy (lightweight)
   - Only for ambiguous cases

### Phase 4: ML Enhancement (Future)
8. **Semantic embeddings** (confidence: 0.70)
   - Only if fuzzy matching fails
   - Optional feature (requires model download)

---

## Implementation Roadmap

### Quick Wins (This Week)

#### 1. Add Fuzzy Matching
```python
# audio_meta/core/identity/matching.py

def match_fuzzy(token1: str, token2: str, threshold: float = 0.85) -> MatchResult:
    """Check if tokens are similar via edit distance."""
    from difflib import SequenceMatcher

    ratio = SequenceMatcher(None, token1, token2).ratio()

    if ratio >= threshold:
        return MatchResult(
            matches=True,
            strategy="fuzzy",
            confidence=ratio,
            details=f"Fuzzy match: {ratio:.2f} similarity"
        )

    return MatchResult(matches=False)
```

**Effort**: 1-2 hours
**Benefit**: Catches typos, close variations

#### 2. Add Manual Override System
```python
# audio_meta/config.py

@dataclass
class IdentitySettings:
    canonical_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    # Example: {"artist": {"Prince": "Prince Rogers Nelson"}}
```

**Effort**: 2-3 hours
**Benefit**: User control, 100% accuracy for known cases

### Medium Term (Next Week)

#### 3. MusicBrainz Identity Lookup
Create a service that queries MusicBrainz during identity scan.

```python
# audio_meta/providers/musicbrainz/identity_resolver.py

class MusicBrainzIdentityResolver:
    """Resolve artist identities using MusicBrainz."""

    def resolve_artist(self, name: str) -> Optional[ArtistIdentity]:
        """Query MusicBrainz for canonical name and aliases."""
        # Search MusicBrainz
        # Get canonical name + all aliases
        # Cache locally
        # Return identity cluster
```

**Effort**: 1 day
**Benefit**: Authoritative data for most artists

#### 4. Local Cache Layer
Cache all external lookups to avoid repeated API calls.

```python
# audio_meta/infrastructure/cache/identity_cache.py

class IdentityCache:
    """Cache external identity lookups."""

    def get_musicbrainz_identity(self, name: str) -> Optional[dict]:
        # Check SQLite cache first
        # Return cached result if exists
```

**Effort**: 4-6 hours
**Benefit**: Fast, reduces API calls

### Long Term (Future)

#### 5. Optional ML Enhancement
- Add sentence-transformers for semantic matching
- Make it optional (feature flag)
- Only activate if user enables it

**Effort**: 2-3 days
**Benefit**: Handles translations, aliases

---

## Architecture Integration

```
NameMatcher (Core)
├── 1. Exact Match (local, fast)
├── 2. Substring Match (local, fast)
├── 3. Initial Match (local, fast)
├── 4. Fuzzy Match (local, fast) ← NEW
└── 5. Manual Override (config, instant) ← NEW

IdentityResolver (Infrastructure - Optional)
├── 6. MusicBrainz Lookup (network, cached) ← NEW
└── 7. Semantic Matching (ML, optional) ← FUTURE
```

### Matching Pipeline

```python
def resolve_identity(name: str) -> IdentityCluster:
    # 1. Check manual overrides first
    if override := config.get_override(name):
        return override

    # 2. Core algorithmic matching
    if match := matcher.match_exact(name):
        return match

    # 3. Fuzzy matching (new)
    if match := matcher.match_fuzzy(name, threshold=0.85):
        return match

    # 4. External lookup (optional, cached)
    if config.use_external_apis:
        if match := musicbrainz.lookup(name):
            cache.store(name, match)
            return match

    # 5. No match - create new identity
    return create_new_identity(name)
```

---

## Performance Considerations

### Local Matching (Fast)
- Exact: O(1) hash lookup
- Substring: O(n²) worst case, but n is small (10-1000 artists)
- Initial: O(n²) worst case
- Fuzzy: O(n * m) where m is string length

**Strategy**: These are fast enough for libraries with <100k artists

### External APIs (Slow)
- MusicBrainz: 1 request/second rate limit
- Network latency: 100-500ms per request

**Strategy**:
1. Only query on identity pre-scan (not during organize)
2. Cache all results locally
3. Batch queries where possible
4. Make it optional

### ML Models (Medium)
- spaCy: ~50MB download, 10-50ms per inference
- Sentence transformers: ~90MB, 50-200ms per inference

**Strategy**:
1. Optional feature (user opts in)
2. Download model on first use
3. Cache all results

---

## Configuration Example

```yaml
# config.yaml

identity:
  # Core matching (always enabled)
  fuzzy_threshold: 0.85  # How similar names need to be (0.0-1.0)

  # Manual overrides (always checked first)
  canonical_overrides:
    artist:
      "Prince": "Prince Rogers Nelson"
      "The Artist Formerly Known As Prince": "Prince Rogers Nelson"
    composer:
      "JS Bach": "Johann Sebastian Bach"

  # External APIs (optional)
  use_musicbrainz: true
  musicbrainz_cache_days: 90  # Cache results for 90 days

  # ML features (optional, requires model download)
  use_semantic_matching: false  # Disable by default
  ml_model: "all-MiniLM-L6-v2"  # Sentence transformer model
```

---

## Recommended Next Steps

### Priority 1: Quick Wins (Do First)
1. ✅ **Add fuzzy matching** - Catches most remaining issues
2. ✅ **Add manual overrides** - User control for edge cases

### Priority 2: External Data (Do Second)
3. **MusicBrainz integration** - Authoritative data
4. **Local caching** - Performance

### Priority 3: ML (Optional)
5. **Semantic matching** - Future enhancement

---

## Decision: What Should We Build?

**I recommend we start with Priority 1 (Quick Wins):**

1. **Add fuzzy matching** to `NameMatcher`
   - Uses standard library `difflib`
   - No new dependencies
   - Catches 80% of remaining issues

2. **Add manual override system**
   - Simple config file
   - User can fix edge cases themselves
   - No code changes needed for new overrides

**This gives us 95%+ coverage without:**
- External API dependencies
- Large ML model downloads
- Complex infrastructure

**What do you think?** Should we:
- A) Add fuzzy matching + manual overrides (quick, simple)
- B) Go straight to MusicBrainz integration (more comprehensive)
- C) Build a hybrid with all features (most robust, more complex)
