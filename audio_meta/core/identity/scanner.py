"""
Identity scanner - pure business logic for identity clustering.

This module contains the core logic for scanning and clustering name variants.
It has NO I/O dependencies - file scanning is injected from outside.

Responsibilities:
- Processing name variants into clusters
- Applying matching strategies (exact, substring, initials)
- Choosing canonical names
- Building identity scan results
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Callable

from .matching import NameMatcher, normalize_token, FEAT_PATTERNS
from .models import IdentityCluster, IdentityScanResult

logger = logging.getLogger(__name__)


class IdentityScanner:
    """
    Scans name variants and builds canonical identity clusters.

    This is pure business logic with no I/O dependencies.
    File reading and metadata extraction are injected from outside.
    """

    def __init__(self) -> None:
        """Initialize the scanner with a name matcher."""
        self.matcher = NameMatcher()

    def scan_names(
        self,
        names_by_category: dict[str, Iterable[str]],
        progress_callback: Callable[[str, int], None] | None = None,
        musicbrainz_resolver=None,
        use_musicbrainz: bool = False,
    ) -> IdentityScanResult:
        """
        Process name variants into clustered identities.

        This is the core scanning logic with NO I/O dependencies.
        All names are provided as input.

        Args:
            names_by_category: Dict mapping category to iterable of names
                Example: {
                    "artist": ["Miles Davis", "Miles Davis", "Beethoven", ...],
                    "composer": ["J.S. Bach", "Johann Sebastian Bach", ...],
                }
            progress_callback: Optional callback(category, count) for progress
            musicbrainz_resolver: Optional MusicBrainz resolver for enhanced matching
            use_musicbrainz: Whether to use MusicBrainz for resolution

        Returns:
            IdentityScanResult with clustered identities
        """
        result = IdentityScanResult()

        # Process each category
        for category, names in names_by_category.items():
            if progress_callback:
                progress_callback(category, 0)

            # Collect raw names by normalized token
            raw_names: dict[str, list[str]] = defaultdict(list)
            count = 0

            for name in names:
                if not name:
                    continue

                # Split multi-name values (e.g., "Artist1, Artist2")
                individual_names = self.split_names(name)

                for individual in individual_names:
                    token = normalize_token(individual)
                    if token:
                        raw_names[token].append(individual)
                        count += 1

            if progress_callback:
                progress_callback(category, count)

            # Build initial clusters
            clusters = self._build_clusters(raw_names, category)

            # Apply matching strategies to merge clusters
            clusters = self.matcher.merge_clusters(
                clusters, category, self.choose_canonical
            )

            # Optional: MusicBrainz resolution for enhanced matching
            if use_musicbrainz and musicbrainz_resolver:
                clusters = self._merge_musicbrainz_clusters(
                    clusters, category, musicbrainz_resolver, progress_callback
                )

            # Store in result
            if category == "artist":
                result.artists = clusters
            elif category == "composer":
                result.composers = clusters
            elif category == "album_artist":
                result.album_artists = clusters
            elif category == "conductor":
                result.conductors = clusters
            elif category == "performer":
                result.performers = clusters

        return result

    def _build_clusters(
        self, raw_names: dict[str, list[str]], category: str
    ) -> dict[str, IdentityCluster]:
        """
        Build identity clusters from raw collected names.

        Each cluster represents one unique entity (artist, composer, etc.)
        with all observed variants.

        Args:
            raw_names: Dict mapping normalized token to list of variants
            category: Category being processed (artist, composer, etc.)

        Returns:
            Dict mapping normalized token to IdentityCluster
        """
        clusters: dict[str, IdentityCluster] = {}

        for token, variants in raw_names.items():
            canonical = self.choose_canonical(variants)
            # Create unique ID by combining category and token
            canonical_id = f"{category}::{token}"

            cluster = IdentityCluster(
                canonical=canonical,
                canonical_id=canonical_id,
                variants=set(variants),
                occurrences=len(variants),
            )
            clusters[token] = cluster

        return clusters

    @staticmethod
    def choose_canonical(variants: list[str]) -> str:
        """
        Choose the best canonical form from a list of variants.

        Selection criteria (in order of importance):
        1. Proper case (not all caps or all lowercase)
        2. Frequency (most common variant)
        3. No comma (avoid "Last, First" format)
        4. Fewer words (prefer individual artists over groups/ensembles)
        5. Shorter length (prefer "Bill Evans" over "Bill Evans Trio")

        Special handling:
        - If best match is "Last, First" and "First Last" exists, use "First Last"
        - Prefer shorter artist names (individual over ensemble)

        Examples:
            ["Bill Evans", "Bill Evans Trio"] → "Bill Evans"
            ["Oscar Peterson", "Dizzy Gillespie Quintet"] → "Oscar Peterson"
            ["J.S. Bach", "Johann Sebastian Bach"] → "Johann Sebastian Bach" (longer wins for initials)

        Args:
            variants: List of name variants

        Returns:
            Best canonical name
        """
        if not variants:
            return ""

        # Count occurrences
        counts: dict[str, int] = defaultdict(int)
        for v in variants:
            counts[v] += 1

        unique = list(counts.keys())

        # Detect if we have initials vs full name (special case)
        # Example: "J.S. Bach" vs "Johann Sebastian Bach"
        has_initials = any("." in name or len(name.split()) > 1 and all(len(w) <= 2 for w in name.split()[:-1]) for name in unique)
        has_full_name = any(len(word) > 2 for name in unique for word in name.split())

        def score(name: str) -> tuple:
            # Calculate scoring factors
            frequency = counts[name]
            is_all_caps = 1 if name.isupper() else 0
            is_all_lower = 1 if name.islower() else 0
            has_proper_case = 1 if not is_all_caps and not is_all_lower else 0
            words = [p for p in name.split() if p]
            word_count = len(words)
            char_count = len(name)

            # Prefer "First Last" over "Last, First" format
            has_comma_space = 1 if ", " in name else 0

            # Check if this looks like initials (e.g., "J.S. Bach")
            is_initials = "." in name or (word_count > 1 and all(len(w) <= 2 for w in words[:-1]))

            # Special case: If we have both initials and full names, prefer full name
            if has_initials and has_full_name and is_initials:
                prefer_full = 1
            else:
                prefer_full = 0

            # Return tuple for sorting (lower is better)
            return (
                -has_proper_case,  # Prefer proper case
                -frequency,  # Prefer more frequent
                has_comma_space,  # Avoid "Last, First"
                is_all_caps,  # Avoid all caps
                is_all_lower,  # Avoid all lowercase
                prefer_full,  # Prefer full name over initials
                word_count,  # Prefer fewer words (individual over ensemble)
                char_count,  # Prefer shorter (individual over ensemble)
                name.casefold(),  # Alphabetical tiebreaker
            )

        unique.sort(key=score)
        best = unique[0]

        # If best has "Last, First" format, try to convert to "First Last"
        if ", " in best:
            parts = best.split(", ", 1)
            if len(parts) == 2:
                first_last = f"{parts[1]} {parts[0]}"
                # Check if this form exists in variants
                if first_last in counts:
                    return first_last

        return best

    @staticmethod
    def split_names(value: str) -> list[str]:
        """
        Split a potentially multi-name value into individual names.

        Handles various delimiter formats:
        - Comma: "Artist1, Artist2" (but NOT "Last, First")
        - Semicolon: "Artist1; Artist2"
        - Slash: "Artist1 / Artist2"
        - Ampersand: "Artist1 & Artist2"
        - "and": "Artist1 and Artist2"

        Special cases:
        - "Last, First" format is NOT split (detected by single comma + capitalization)
        - "Various Artists" and similar are filtered out
        - Featuring patterns are removed

        Args:
            value: Name value (potentially containing multiple names)

        Returns:
            List of individual names
        """
        if not value:
            return []

        # Step 1: Detect if this looks like "Last, First" format
        is_lastname_first = False
        comma_count = value.count(",")
        if comma_count == 1 and ";" not in value and "/" not in value and "&" not in value:
            parts = value.split(",", 1)
            if len(parts) == 2 and parts[1].strip() and parts[1].strip()[0].isupper():
                # Likely "Last, First" - don't split on comma
                is_lastname_first = True

        # Step 2: Normalize delimiters
        normalized = value

        # Replace common multi-artist delimiters with semicolon
        if not is_lastname_first:
            # Treat comma as delimiter ONLY if it's not "Last, First" format
            normalized = normalized.replace(",", ";")

        # Other delimiters
        normalized = normalized.replace(" / ", ";")
        normalized = normalized.replace("/", ";")
        normalized = normalized.replace(" & ", ";")
        normalized = normalized.replace(" and ", ";")

        # Step 3: Split on semicolon
        parts = [p.strip() for p in normalized.split(";") if p.strip()]

        # Step 4: Clean each part
        cleaned = []
        for part in parts:
            # Remove featuring/feat patterns
            for pattern in FEAT_PATTERNS:
                part = re.sub(pattern, "", part, flags=re.IGNORECASE)
            part = part.strip()

            # Skip common non-person values
            if part.lower() in ("various", "various artists", "unknown", "n/a", ""):
                continue

            if part:
                cleaned.append(part)

        return cleaned

    def _merge_musicbrainz_clusters(
        self,
        clusters: dict[str, IdentityCluster],
        category: str,
        musicbrainz_resolver,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> dict[str, IdentityCluster]:
        """
        Merge clusters using MusicBrainz data.

        This queries MusicBrainz for each cluster's canonical name and merges
        clusters that have the same MusicBrainz ID.

        Args:
            clusters: Current clusters
            category: Category being processed
            musicbrainz_resolver: MusicBrainz resolver instance
            progress_callback: Optional progress callback

        Returns:
            Merged clusters
        """
        if not clusters or not musicbrainz_resolver:
            return clusters

        logger.info(
            f"Resolving {len(clusters)} {category} clusters via MusicBrainz..."
        )

        # Group clusters by MusicBrainz ID
        mb_groups: dict[str, list[str]] = {}  # mb_id -> [tokens]
        unresolved_tokens = []

        for token, cluster in clusters.items():
            # Query MusicBrainz for this cluster's canonical name
            identity = musicbrainz_resolver.search_artist(
                cluster.canonical,
                strict=False,  # Accept lower confidence matches
                use_cache=True
            )

            if identity and identity.mb_id:
                # Group by MusicBrainz ID
                mb_id = identity.mb_id
                if mb_id not in mb_groups:
                    mb_groups[mb_id] = []
                mb_groups[mb_id].append(token)

                # Update canonical name to MusicBrainz canonical
                # (but only if confidence is high)
                if identity.confidence >= 0.9:
                    cluster.canonical = identity.canonical_name
                    # Add all MusicBrainz aliases to variants
                    cluster.variants.update(identity.aliases)
            else:
                unresolved_tokens.append(token)

        # Merge clusters with same MusicBrainz ID
        merged = dict(clusters)
        merge_count = 0

        for mb_id, tokens in mb_groups.items():
            if len(tokens) > 1:
                # Merge into first token
                primary_token = tokens[0]

                for secondary_token in tokens[1:]:
                    if secondary_token in merged:
                        # Merge variants
                        merged[primary_token].variants.update(
                            merged[secondary_token].variants
                        )
                        merged[primary_token].occurrences += (
                            merged[secondary_token].occurrences
                        )

                        # Re-choose canonical (now includes MB data)
                        all_variants = list(merged[primary_token].variants)
                        merged[primary_token].canonical = self.choose_canonical(
                            all_variants
                        )

                        # Remove secondary
                        del merged[secondary_token]
                        merge_count += 1

                        logger.info(
                            f"Merged {category} clusters via MusicBrainz "
                            f"(MB:{mb_id}): '{secondary_token}' → '{primary_token}' "
                            f"(canonical: {merged[primary_token].canonical})"
                        )

        logger.info(
            f"MusicBrainz {category} resolution: "
            f"{len(mb_groups)} groups found, {merge_count} clusters merged, "
            f"{len(unresolved_tokens)} unresolved"
        )

        return merged
