"""
Name matching algorithms for identity canonicalization.

This module provides various strategies for matching name variants:
- Exact matching (same normalized token)
- Substring matching (one token contains another)
- Initial matching (initials match full name) - NEW
- Fuzzy matching (Levenshtein distance) - FUTURE

All functions are pure and have no I/O dependencies.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .models import MatchResult, IdentityCluster


# Feature patterns to remove during normalization
FEAT_PATTERNS = [
    r'\s*\(?\s*feat\.?\s+[^)]+\)?',
    r'\s*\(?\s*featuring\s+[^)]+\)?',
    r'\s*\(?\s*ft\.?\s+[^)]+\)?',
    r'\s*\(?\s*with\s+[^)]+\)?',
]


def normalize_token(value: str) -> str:
    """
    Normalize a name to a token for clustering.

    This creates a unique identifier while preserving enough information
    to distinguish different people.

    Process:
    1. Remove featuring patterns
    2. Unicode normalization (NFKD)
    3. Convert to ASCII
    4. Lowercase
    5. Remove punctuation (keep spaces temporarily)
    6. Collapse spaces
    7. Remove all spaces for final token

    Examples:
        "Ludwig van Beethoven" → "ludwigvonbeethoven"
        "Art Blakey & The Jazz Messengers" → "artblakeythejazzmessengers"
        "Yo-Yo Ma" → "yoyoma"

    Args:
        value: The name to normalize

    Returns:
        Normalized token (alphanumeric, lowercase, no spaces)
    """
    if not value:
        return ""

    # Remove featuring patterns first
    cleaned = value
    for pattern in FEAT_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Unicode normalization
    normalized = unicodedata.normalize("NFKD", cleaned)

    # Convert to ASCII
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Lowercase
    ascii_only = ascii_only.lower()

    # Remove punctuation but keep spaces temporarily
    ascii_only = re.sub(r"[^a-z0-9\s]+", " ", ascii_only)

    # Collapse multiple spaces
    ascii_only = re.sub(r"\s+", " ", ascii_only).strip()

    # Remove spaces for final token (but we kept them to preserve word boundaries)
    token = ascii_only.replace(" ", "")

    return token if token else ""


def extract_words(value: str) -> list[str]:
    """
    Extract words from a name for initial matching.

    Handles various name formats:
    - "Johann Sebastian Bach" → ["johann", "sebastian", "bach"]
    - "J.S. Bach" → ["j", "s", "bach"]
    - "Ludwig van Beethoven" → ["ludwig", "van", "beethoven"]
    - "Bach, J.S." → ["bach", "j", "s"]

    Args:
        value: The name to extract words from

    Returns:
        List of normalized words
    """
    if not value:
        return []

    # Normalize to ASCII
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Split on common delimiters (spaces, commas, periods)
    # Keep each part
    parts = re.split(r'[,.\s]+', ascii_only.lower())

    # Filter out empty strings and very short non-letter parts
    words = [p.strip() for p in parts if p.strip()]

    return words


def extract_initials(words: list[str]) -> str:
    """
    Extract initials from a list of words.

    Examples:
        ["johann", "sebastian", "bach"] → "jsb"
        ["ludwig", "van", "beethoven"] → "lvb"
        ["j", "s", "bach"] → "jsb" (already initials)

    Args:
        words: List of words

    Returns:
        String of initials (lowercase)
    """
    if not words:
        return ""

    # Take first letter of each word
    initials = "".join(word[0] for word in words if word)

    return initials.lower()


def match_initials(short_token: str, long_token: str, short_words: list[str], long_words: list[str]) -> MatchResult:
    """
    Check if short name matches long name using initial matching.

    This handles cases like:
    - "J.S. Bach" vs "Johann Sebastian Bach"
    - "W.A. Mozart" vs "Wolfgang Amadeus Mozart"
    - "L.v. Beethoven" vs "Ludwig van Beethoven"

    Strategy:
    1. Extract initials from long name
    2. Check if short token matches those initials
    3. Verify last word matches (surname)

    Examples:
        short="jsbach", long="johannsebastianbach"
        → short_words=["j","s","bach"], long_words=["johann","sebastian","bach"]
        → long_initials="jsb", short matches "jsb..." ✅

    Args:
        short_token: Normalized token of shorter name
        long_token: Normalized token of longer name
        short_words: Word list of shorter name
        long_words: Word list of longer name

    Returns:
        MatchResult indicating if they match via initials
    """
    # Need at least 2 words in both for meaningful initial matching
    if len(short_words) < 2 or len(long_words) < 2:
        return MatchResult(matches=False)

    # Extract initials from long name
    long_initials = extract_initials(long_words)

    # Check if short token starts with long initials + has matching surname
    # Example: short="jsbach", long_initials="jsb", long_words[-1]="bach"
    # Check: "jsbach".startswith("jsb") AND short ends with "bach"

    # Method 1: Check if short is basically "initials + surname"
    surname = long_words[-1]
    expected_short = long_initials[:-1] + surname  # All initials except last + full surname
    # Example: "js" + "bach" = "jsbach"

    if short_token == expected_short:
        return MatchResult(
            matches=True,
            strategy="initial_exact",
            confidence=0.95,
            details=f"Short name '{short_token}' matches initials+surname pattern from '{long_token}'"
        )

    # Method 2: Check if short words are initials of long words
    # Compare word by word (except last, which should match exactly or be initial)
    if len(short_words) == len(long_words):
        all_match = True
        for i, (short_word, long_word) in enumerate(zip(short_words[:-1], long_words[:-1])):
            # Each short word should be either:
            # 1. An initial of the long word (short_word[0] == long_word[0])
            # 2. Or the same word
            if len(short_word) == 1:
                # It's an initial - check if it matches
                if short_word[0] != long_word[0]:
                    all_match = False
                    break
            elif short_word != long_word:
                # It's a full word but doesn't match
                all_match = False
                break

        # Last word should match exactly or short should be initial of long
        last_short = short_words[-1]
        last_long = long_words[-1]

        if len(last_short) == 1:
            # Last word is initial
            if last_short[0] != last_long[0]:
                all_match = False
        elif last_short != last_long:
            # Last word doesn't match
            all_match = False

        if all_match:
            return MatchResult(
                matches=True,
                strategy="initial_wordwise",
                confidence=0.90,
                details=f"Word-by-word initial match: '{short_words}' vs '{long_words}'"
            )

    return MatchResult(matches=False)


def match_substring(short_token: str, long_token: str) -> MatchResult:
    """
    Check if short token is a substring of long token.

    Examples:
        "beethoven" in "ludwigvonbeethoven" → True
        "miles" in "milesdavis" → True
        "beatles" in "thebeatles" → True

    Args:
        short_token: Shorter normalized token
        long_token: Longer normalized token

    Returns:
        MatchResult indicating if short is substring of long
    """
    if short_token in long_token:
        return MatchResult(
            matches=True,
            strategy="substring",
            confidence=0.85,
            details=f"'{short_token}' is substring of '{long_token}'"
        )

    return MatchResult(matches=False)


def match_exact(token1: str, token2: str) -> MatchResult:
    """
    Check if two tokens match exactly.

    Args:
        token1: First normalized token
        token2: Second normalized token

    Returns:
        MatchResult indicating exact match
    """
    if token1 == token2:
        return MatchResult(
            matches=True,
            strategy="exact",
            confidence=1.0,
            details=f"Exact token match: '{token1}'"
        )

    return MatchResult(matches=False)


class NameMatcher:
    """
    Handles all name matching strategies for identity canonicalization.

    Usage:
        matcher = NameMatcher()
        result = matcher.match("J.S. Bach", "Johann Sebastian Bach")
        if result.matches:
            print(f"Matched via {result.strategy}")
    """

    def match(self, name1: str, name2: str) -> MatchResult:
        """
        Determine if two names match using various strategies.

        Strategies are applied in order of confidence:
        1. Exact token match (confidence: 1.0)
        2. Substring match (confidence: 0.85)
        3. Initial match (confidence: 0.90-0.95)

        Args:
            name1: First name variant
            name2: Second name variant

        Returns:
            MatchResult with match status and details
        """
        token1 = normalize_token(name1)
        token2 = normalize_token(name2)

        if not token1 or not token2:
            return MatchResult(matches=False, details="Empty token(s)")

        # Strategy 1: Exact match
        result = match_exact(token1, token2)
        if result.matches:
            return result

        # Determine which is shorter for substring and initial matching
        if len(token1) <= len(token2):
            short_token, long_token = token1, token2
            short_name, long_name = name1, name2
        else:
            short_token, long_token = token2, token1
            short_name, long_name = name2, name1

        # Strategy 2: Substring match
        result = match_substring(short_token, long_token)
        if result.matches:
            return result

        # Strategy 3: Initial match
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)
        result = match_initials(short_token, long_token, short_words, long_words)
        if result.matches:
            return result

        return MatchResult(matches=False, details="No matching strategy succeeded")

    def merge_clusters(
        self,
        clusters: dict[str, IdentityCluster],
        category: str,
        choose_canonical_fn
    ) -> dict[str, IdentityCluster]:
        """
        Merge identity clusters using all matching strategies.

        This applies matching strategies in sequence:
        1. Substring matching (already clusters with same token)
        2. Initial matching (NEW - catches "J.S. Bach" vs "Johann Sebastian Bach")

        Args:
            clusters: Existing clusters keyed by normalized token
            category: Category being processed (artist, composer, etc.)
            choose_canonical_fn: Function to choose best canonical name from variants

        Returns:
            Merged clusters
        """
        if len(clusters) < 2:
            return clusters

        # First pass: substring merging (existing logic)
        merged = self._merge_substring_clusters(clusters, category, choose_canonical_fn)

        # Second pass: initial matching (NEW)
        merged = self._merge_initial_clusters(merged, category, choose_canonical_fn)

        return merged

    def _merge_substring_clusters(
        self,
        clusters: dict[str, IdentityCluster],
        category: str,
        choose_canonical_fn
    ) -> dict[str, IdentityCluster]:
        """
        Merge clusters where one token is a substring of another.

        Example: "beethoven" is a substring of "ludwigvonbeethoven"
        """
        if len(clusters) < 2:
            return clusters

        # Sort tokens by length (shortest first)
        tokens = sorted(clusters.keys(), key=len)
        merged = {}
        merged_into: dict[str, str] = {}  # Maps token -> merged_token

        for i, short_token in enumerate(tokens):
            # Skip if already merged
            if short_token in merged_into:
                continue

            # Find longer tokens that contain this token
            for long_token in tokens[i + 1:]:
                if long_token in merged_into:
                    continue

                # Check if short token is a substring of long token
                if short_token in long_token:
                    # Merge: keep the cluster with more variants or longer canonical name
                    short_cluster = clusters[short_token]
                    long_cluster = clusters[long_token]

                    # Prefer the cluster with more occurrences, or longer canonical name
                    if short_cluster.occurrences >= long_cluster.occurrences:
                        # Keep short, merge long into it
                        primary_token = short_token
                        secondary_cluster = long_cluster
                    else:
                        # Keep long, merge short into it
                        primary_token = long_token
                        secondary_cluster = short_cluster

                    # Get primary cluster
                    if primary_token not in merged:
                        merged[primary_token] = clusters[primary_token]

                    # Merge variants from secondary into primary
                    merged[primary_token].variants.update(secondary_cluster.variants)
                    merged[primary_token].occurrences += secondary_cluster.occurrences

                    # Re-choose canonical from combined variants
                    all_variants = list(merged[primary_token].variants)
                    merged[primary_token].canonical = choose_canonical_fn(all_variants)

                    # Track that the other token was merged
                    other_token = long_token if primary_token == short_token else short_token
                    merged_into[other_token] = primary_token

                    break  # Move to next short token

            # If not merged with anything, keep as is
            if short_token not in merged_into and short_token not in merged:
                merged[short_token] = clusters[short_token]

        return merged

    def _merge_initial_clusters(
        self,
        clusters: dict[str, IdentityCluster],
        category: str,
        choose_canonical_fn
    ) -> dict[str, IdentityCluster]:
        """
        Merge clusters where initials match full names.

        Example: "jsbach" (J.S. Bach) matches "johannsebastianbach" (Johann Sebastian Bach)

        This is the NEW enhancement that catches classical composer variants.
        """
        if len(clusters) < 2:
            return clusters

        tokens = list(clusters.keys())
        merged = dict(clusters)  # Start with existing clusters
        merged_into: dict[str, str] = {}  # Maps token -> merged_token

        # Try all pairs
        for i, token1 in enumerate(tokens):
            if token1 in merged_into:
                continue

            for token2 in tokens[i + 1:]:
                if token2 in merged_into:
                    continue

                # Determine which is shorter
                if len(token1) <= len(token2):
                    short_token, long_token = token1, token2
                else:
                    short_token, long_token = token2, token1

                # Get example names for word extraction
                short_cluster = merged.get(short_token) or clusters.get(short_token)
                long_cluster = merged.get(long_token) or clusters.get(long_token)

                if not short_cluster or not long_cluster:
                    continue

                # Get representative names
                short_name = short_cluster.canonical
                long_name = long_cluster.canonical

                # Check initial match
                short_words = extract_words(short_name)
                long_words = extract_words(long_name)

                result = match_initials(short_token, long_token, short_words, long_words)

                if result.matches:
                    # Merge long into short or vice versa based on occurrences
                    if short_cluster.occurrences >= long_cluster.occurrences:
                        primary_token = short_token
                        secondary_token = long_token
                        secondary_cluster = long_cluster
                    else:
                        primary_token = long_token
                        secondary_token = short_token
                        secondary_cluster = short_cluster

                    # Merge variants
                    merged[primary_token].variants.update(secondary_cluster.variants)
                    merged[primary_token].occurrences += secondary_cluster.occurrences

                    # Re-choose canonical
                    all_variants = list(merged[primary_token].variants)
                    merged[primary_token].canonical = choose_canonical_fn(all_variants)

                    # Remove merged cluster
                    if secondary_token in merged:
                        del merged[secondary_token]

                    merged_into[secondary_token] = primary_token

        return merged
