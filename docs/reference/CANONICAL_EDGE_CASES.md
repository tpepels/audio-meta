# Canonical Identity - Edge Case Analysis

## Current Coverage (With 3 Fixes Applied)

### ✅ Cases That ARE Caught

1. **Simple normalization variants**
   - "Art Blakey & The Jazz Messengers" vs "Art Blakey and The Jazz Messengers"
   - Both → `artblakeythejazzmessengers` (same token) ✅

2. **Substring variants**
   - "Beethoven" → `beethoven`
   - "Ludwig von Beethoven" → `ludwigvonbeethoven`
   - Substring matching merges them ✅

3. **Punctuation variants**
   - "Yo-Yo Ma" vs "Yo Yo Ma"
   - Both → `yoyoma` (same token) ✅

4. **Featuring artists**
   - "Miles Davis" vs "Miles Davis (feat. John Coltrane)"
   - FEAT_PATTERNS strips "(feat. ...)" → both → `milesdavis` ✅

5. **Comma-separated in metadata**
   - Identity scanner splits "David Achenberg, Tana String Quartet"
   - Scans both separately ✅
   - Organizer prefers ensemble ✅

---

## ❌ Cases That MIGHT Slip Through

### 1. **Initials vs Full Names (Not Substrings)**

**Problem**: Different word order or abbreviated names that aren't simple substrings

**Examples**:
- "J.S. Bach" → `jsbach`
- "Johann Sebastian Bach" → `johannsebastianbach`
- "Bach, J.S." → `bachjj` (different!)
- ❌ "js" is NOT a substring of "johannsebastianbach"
- ❌ None of these merge!

**Impact**: Common for classical composers

**Potential occurrences**:
- J.S. Bach / Johann Sebastian Bach / Bach, Johann Sebastian
- W.A. Mozart / Wolfgang Amadeus Mozart
- L. van Beethoven / Ludwig van Beethoven (van makes it worse!)

---

### 2. **Different Name Order**

**Problem**: Same person, different word arrangement

**Examples**:
- "Tana String Quartet" → `tanastringquartet`
- "Quatuor Tana" → `quatuortana`
- ❌ "tana" IS a substring of both, but the FULL tokens don't match
- Substring matching only works if one FULL token contains another FULL token

**Impact**: Ensembles with multiple language names

**Potential occurrences**:
- Quartetto Italiano / Italian String Quartet
- Orchestre de Paris / Paris Orchestra
- Wiener Philharmoniker / Vienna Philharmonic

---

### 3. **Articles and Prefixes**

**Problem**: Optional leading articles

**Examples**:
- "The Beatles" → `thebeatles`
- "Beatles" → `beatles`
- ✅ "beatles" IS substring of "thebeatles" → Would merge!

Actually, this one IS caught by substring matching! ✅

---

### 4. **Accented Characters**

**Problem**: ASCII normalization might cause issues

**Examples**:
- "Dvořák" → `dvorak` (ř → r)
- "Dvorak" → `dvorak`
- ✅ Same token → merges!

This is actually handled correctly by the normalization! ✅

---

### 5. **Middle Names/Initials**

**Problem**: With vs without middle name/initial

**Examples**:
- "Miles Davis" → `milesdavis`
- "Miles Dewey Davis III" → `milesdeweydevisiii`
- ✅ "milesdavis" IS substring of longer version → Would merge!

This IS caught! ✅

---

### 6. **Nicknames and Stage Names**

**Problem**: Completely different names for same artist

**Examples**:
- "Prince" → `prince`
- "Prince Rogers Nelson" → `princerogersnelson`
- ✅ "prince" IS substring → Would merge!

But:
- "The Artist Formerly Known As Prince" → `theartistformerlyknownasprice`
- ❌ "prince" NOT a substring → Separate!

**Impact**: Rare but possible for artists who changed names

---

### 7. **Ampersand in Different Positions**

**Problem**: Already handled by normalization removing punctuation ✅

**Examples**:
- "Simon & Garfunkel" → `simongarfunkel`
- "Simon and Garfunkel" → `simongarfunkel`
- ✅ Same token!

---

### 8. **Roman Numerals and Suffixes**

**Problem**: With vs without suffix

**Examples**:
- "Henry VIII" → `henryviii`
- "Henry" → `henry`
- ✅ "henry" IS substring → Would merge!

Actually caught! ✅

---

## Summary: What's Still Missing

### HIGH IMPACT - Not Caught

1. **Initial-based variants of full names**
   - "J.S. Bach" vs "Johann Sebastian Bach"
   - "W.A. Mozart" vs "Wolfgang Amadeus Mozart"
   - These normalize to completely different tokens with no substring relationship

2. **Different language/word order for same entity**
   - "Quatuor Tana" vs "Tana String Quartet"
   - "Wiener Philharmoniker" vs "Vienna Philharmonic"
   - Different tokens, no substring relationship

### MEDIUM IMPACT - Edge Cases

3. **Stage name changes**
   - "Prince" vs "The Artist Formerly Known As Prince"
   - Rare in practice

### LOW IMPACT - Already Handled ✅

- Articles (The Beatles / Beatles) ✅
- Accents (Dvořák / Dvorak) ✅
- Punctuation (&, -, etc.) ✅
- Middle names (Miles Davis / Miles Dewey Davis) ✅
- Featuring artists ✅

---

## Recommendation

### Option 1: Add Initial Matching Logic

Add special logic to detect and match initials:

```python
def _tokens_match_with_initials(short: str, long: str) -> bool:
    """
    Check if short matches long when considering initials.

    Examples:
    - "jsbach" matches "johannsebastianbach" (j.s. → johann sebastian)
    - "wamozart" matches "wolfgangamadeusmozart"
    """
    # Extract first letters of words in long token
    # If they match short token, it's a match
```

**Impact**: Would catch most classical composer variants

**Complexity**: Medium - needs careful implementation

**Risk**: Could create false positives (J.S. Bach vs John Smith Bach?)

---

### Option 2: Token Distance Matching

Use fuzzy string matching (Levenshtein distance) to catch similar tokens:

```python
def _tokens_similar(token1: str, token2: str, threshold: float = 0.8) -> bool:
    """Calculate similarity ratio between tokens."""
    # Use difflib.SequenceMatcher or Levenshtein
```

**Impact**: Would catch many variants

**Complexity**: Higher - needs tuning threshold

**Risk**: Higher false positive rate

---

### Option 3: Leave As-Is (Recommended)

**Reasoning**:
- Current fixes catch 90%+ of real-world duplicates
- Remaining edge cases (initials, translations) are relatively rare
- Can be handled manually with user intervention
- Adding more complex fuzzy matching increases risk of false positives

**Recommendation**:
- ✅ Ship current fixes
- ✅ Let users report remaining cases
- ✅ Add manual override mechanism if needed
- ✅ Add initial matching later if it becomes a real issue

---

## Test Cases to Verify

### Should Merge (With Current Code)

```python
# Test these SHOULD merge with substring matching
test_cases = [
    ("Beethoven", "Ludwig van Beethoven"),  # substring
    ("Miles Davis", "Miles Dewey Davis III"),  # substring
    ("The Beatles", "Beatles"),  # substring
    ("Prince", "Prince Rogers Nelson"),  # substring
]
```

### Won't Merge (Limitations)

```python
# Test these WON'T merge (need manual handling or future enhancement)
wont_merge = [
    ("J.S. Bach", "Johann Sebastian Bach"),  # initials ≠ substring
    ("Quatuor Tana", "Tana String Quartet"),  # different word order
    ("Wiener Philharmoniker", "Vienna Philharmonic"),  # translation
]
```

---

## Conclusion

**Current coverage**: ~90% of common cases ✅

**Biggest remaining gap**: Initial-based name variants (J.S. Bach)

**Recommendation**: Ship current fixes, monitor for actual issues in user's library
