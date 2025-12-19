"""
Identity canonicalization module.

This module provides pure business logic for applying canonical identity mappings.
It depends on the cache infrastructure but has no other I/O dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .models import IdentityScanResult
from .scanner import IdentityScanner

if TYPE_CHECKING:
    from pathlib import Path


class CanonicalCache(Protocol):
    """Protocol for canonical name cache interface."""

    def get_canonical_name(self, key: str) -> str | None:
        """Retrieve canonical name for a cache key."""
        ...

    def set_canonical_name(self, key: str, canonical: str) -> None:
        """Store canonical name mapping."""
        ...


class IdentityCanonicalizer:
    """
    Applies canonical identity mappings during metadata processing.

    This class provides two key operations:
    1. Persisting scan results to cache (apply_scan_results)
    2. Canonicalizing names during runtime (canonicalize, canonicalize_multi)
    """

    def __init__(self, cache: CanonicalCache) -> None:
        """
        Initialize the canonicalizer.

        Args:
            cache: Cache implementation for storing/retrieving canonical names
        """
        self.cache = cache
        self._local_cache: dict[str, str] = {}

    def apply_scan_results(self, result: IdentityScanResult) -> int:
        """
        Persist scan results to the cache for use during matching.

        Args:
            result: The identity scan result containing clustered identities

        Returns:
            The number of canonical mappings created
        """
        count = 0

        for category, clusters in result.all_people.items():
            for token, cluster in clusters.items():
                # Use the canonical_id as the key (ensures uniqueness)
                cache_key = cluster.canonical_id

                # Store the user-friendly canonical name
                self.cache.set_canonical_name(cache_key, cluster.canonical)
                count += 1

                # Store all variants that map to this canonical
                for variant in cluster.variants:
                    variant_token = IdentityScanner._normalize_token(variant)
                    if variant_token and variant_token != token:
                        variant_key = f"{category}::{variant_token}"
                        self.cache.set_canonical_name(variant_key, cluster.canonical)
                        count += 1

        return count

    def canonicalize(self, name: str, category: str = "artist") -> str:
        """
        Get the canonical form of a name.

        Args:
            name: The name to canonicalize
            category: One of 'artist', 'composer', 'album_artist', 'conductor', 'performer'

        Returns:
            The canonical form, or the original name if no mapping exists
        """
        if not name:
            return name

        # Strip whitespace
        name = name.strip()

        token = IdentityScanner._normalize_token(name)
        if not token:
            return name

        cache_key = f"{category}::{token}"

        # Check local cache first
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]

        # Check persistent cache
        canonical = self.cache.get_canonical_name(cache_key)
        if canonical:
            self._local_cache[cache_key] = canonical
            return canonical

        return name

    def canonicalize_multi(
        self, names: str, category: str = "artist", library_roots: list[Path] | None = None
    ) -> str:
        """
        Canonicalize a potentially multi-name string.

        Args:
            names: String containing one or more names (possibly comma or semicolon separated)
            category: Identity category (artist, composer, etc.)
            library_roots: Optional library roots for creating scanner (required for name splitting)

        Returns:
            Canonicalized names separated by "; " (NOT commas)

        Note:
            This method returns individual names separated by "; " to maintain
            consistency with the scanner's output format.
        """
        if not names:
            return names

        # Split using the same logic as scanner
        # We need a scanner instance just for the name splitting utility
        # TODO: This creates a coupling - consider extracting name splitting to a pure function
        from ...config import LibrarySettings

        scanner = IdentityScanner(LibrarySettings(roots=library_roots or []), None)
        parts = scanner._split_names(names)

        canonical_parts = []
        seen = set()

        for part in parts:
            if part:
                canonical = self.canonicalize(part, category)
                # Avoid duplicates
                canonical_lower = canonical.lower()
                if canonical_lower not in seen:
                    canonical_parts.append(canonical)
                    seen.add(canonical_lower)

        # Always use semicolon as separator (NEVER comma)
        return "; ".join(canonical_parts) if canonical_parts else names
