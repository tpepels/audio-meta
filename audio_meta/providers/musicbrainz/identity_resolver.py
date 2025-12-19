"""
MusicBrainz identity resolver for artist canonicalization.

Uses MusicBrainz API to resolve artist names to canonical forms with aliases.
Respects rate limits (1 request/second) and caches results locally.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Optional dependency - gracefully handle if not installed
try:
    import musicbrainzngs
    MUSICBRAINZ_AVAILABLE = True
except ImportError:
    MUSICBRAINZ_AVAILABLE = False
    logger.warning("musicbrainzngs not installed - MusicBrainz identity resolution disabled")


@dataclass
class ArtistIdentity:
    """
    MusicBrainz artist identity with aliases.

    Attributes:
        mb_id: MusicBrainz artist ID (UUID)
        canonical_name: Primary/official artist name
        aliases: All known name variants
        artist_type: Type (Person, Group, Orchestra, etc.)
        sort_name: Name for sorting (usually "Last, First" for people)
        disambiguation: Disambiguating comment (e.g., "jazz pianist")
        confidence: Search match confidence (0.0-1.0)
    """
    mb_id: str
    canonical_name: str
    aliases: list[str]
    artist_type: str
    sort_name: Optional[str] = None
    disambiguation: Optional[str] = None
    confidence: float = 1.0

    def has_alias(self, name: str) -> bool:
        """Check if a name is an alias of this artist."""
        name_lower = name.lower()
        return any(alias.lower() == name_lower for alias in self.aliases)


class MusicBrainzIdentityResolver:
    """
    Resolve artist identities using MusicBrainz.

    This service queries MusicBrainz to get canonical artist names and aliases.
    Results are cached to minimize API calls (rate limited to 1/second).

    Usage:
        resolver = MusicBrainzIdentityResolver(cache)
        identity = resolver.search_artist("Art Blakey & The Jazz Messengers")

        if identity:
            print(f"Canonical: {identity.canonical_name}")
            print(f"Aliases: {identity.aliases}")
    """

    def __init__(self, cache=None, user_agent: str = "audio-meta/1.0"):
        """
        Initialize the MusicBrainz identity resolver.

        Args:
            cache: Optional cache for storing results
            user_agent: User agent string for MusicBrainz API
        """
        self.cache = cache
        self._last_request_time = 0.0
        self._request_delay = 1.1  # Slightly over 1 second to be safe

        if not MUSICBRAINZ_AVAILABLE:
            logger.error("MusicBrainz resolver created but musicbrainzngs not available")
            return

        # Configure MusicBrainz client
        musicbrainzngs.set_useragent(
            "audio-meta",
            "1.0",
            "https://github.com/yourusername/audio-meta"
        )

    def is_available(self) -> bool:
        """Check if MusicBrainz is available."""
        return MUSICBRAINZ_AVAILABLE

    def _respect_rate_limit(self) -> None:
        """
        Ensure we don't exceed rate limit (1 request/second).

        Sleeps if necessary to maintain rate limit.
        """
        elapsed = time.time() - self._last_request_time
        if elapsed < self._request_delay:
            sleep_time = self._request_delay - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self._last_request_time = time.time()

    def search_artist(
        self,
        name: str,
        strict: bool = False,
        use_cache: bool = True
    ) -> Optional[ArtistIdentity]:
        """
        Search MusicBrainz for an artist and return identity.

        Args:
            name: Artist name to search
            strict: If True, only return high-confidence matches (score >= 90)
            use_cache: If True, check cache before querying API

        Returns:
            ArtistIdentity if found, None otherwise
        """
        if not MUSICBRAINZ_AVAILABLE:
            return None

        if not name or not name.strip():
            return None

        name = name.strip()

        # Check cache first
        if use_cache and self.cache:
            cached = self._get_from_cache(name)
            if cached:
                logger.debug(f"Cache hit for artist: {name}")
                return cached

        try:
            # Respect rate limit
            self._respect_rate_limit()

            # Search MusicBrainz
            logger.info(f"Searching MusicBrainz for artist: {name}")
            result = musicbrainzngs.search_artists(
                artist=name,
                limit=5  # Get top 5 matches
            )

            if not result.get('artist-list'):
                logger.debug(f"No MusicBrainz results for: {name}")
                return None

            # Get best match (first result)
            artist_data = result['artist-list'][0]

            # Check confidence score
            score = int(artist_data.get('ext:score', '0'))
            confidence = score / 100.0

            if strict and score < 90:
                logger.debug(f"Low confidence match ({score}) for: {name}")
                return None

            # Get full artist details with aliases
            artist_id = artist_data['id']

            # Respect rate limit for second request
            self._respect_rate_limit()

            full_data = musicbrainzngs.get_artist_by_id(
                artist_id,
                includes=['aliases', 'tags']
            )

            artist = full_data['artist']

            # Extract aliases
            aliases = [artist['name']]  # Include primary name

            if 'alias-list' in artist:
                for alias_data in artist['alias-list']:
                    alias_name = alias_data.get('alias', alias_data.get('name'))
                    if alias_name and alias_name not in aliases:
                        aliases.append(alias_name)

            identity = ArtistIdentity(
                mb_id=artist['id'],
                canonical_name=artist['name'],
                aliases=aliases,
                artist_type=artist.get('type', 'Unknown'),
                sort_name=artist.get('sort-name'),
                disambiguation=artist.get('disambiguation'),
                confidence=confidence
            )

            # Cache result
            if self.cache:
                self._store_in_cache(name, identity)

            logger.info(
                f"Resolved '{name}' → '{identity.canonical_name}' "
                f"(type: {identity.artist_type}, {len(aliases)} aliases, "
                f"confidence: {confidence:.0%})"
            )

            return identity

        except Exception as exc:
            logger.warning(f"MusicBrainz lookup failed for '{name}': {exc}")
            return None

    def resolve_variants(
        self,
        names: list[str],
        progress_callback: Optional[callable] = None
    ) -> dict[str, ArtistIdentity]:
        """
        Resolve multiple name variants to canonical identities.

        This respects rate limits and caches results.

        Args:
            names: List of artist names to resolve
            progress_callback: Optional callback(current, total, name)

        Returns:
            Dict mapping original name to ArtistIdentity
        """
        if not MUSICBRAINZ_AVAILABLE:
            logger.error("Cannot resolve variants: MusicBrainz not available")
            return {}

        results = {}
        total = len(names)

        for i, name in enumerate(names, 1):
            if progress_callback:
                progress_callback(i, total, name)

            identity = self.search_artist(name)
            if identity:
                results[name] = identity

        return results

    def merge_by_musicbrainz_id(
        self,
        clusters: dict[str, any]
    ) -> dict[str, any]:
        """
        Merge identity clusters that have the same MusicBrainz ID.

        Args:
            clusters: Dict of identity clusters

        Returns:
            Merged clusters
        """
        # Group by MusicBrainz ID
        mb_groups: dict[str, list[str]] = {}

        for token, cluster in clusters.items():
            # Try to get MusicBrainz ID from cache
            identity = self.search_artist(cluster.canonical, use_cache=True)

            if identity:
                mb_id = identity.mb_id
                if mb_id not in mb_groups:
                    mb_groups[mb_id] = []
                mb_groups[mb_id].append(token)

        # Merge clusters with same MB ID
        merged = dict(clusters)

        for mb_id, tokens in mb_groups.items():
            if len(tokens) > 1:
                # Merge into first token
                primary = tokens[0]
                for secondary in tokens[1:]:
                    if secondary in merged:
                        # Merge variants
                        merged[primary].variants.update(merged[secondary].variants)
                        merged[primary].occurrences += merged[secondary].occurrences

                        # Remove secondary
                        del merged[secondary]

                        logger.info(
                            f"Merged clusters via MusicBrainz ID {mb_id}: "
                            f"{secondary} → {primary}"
                        )

        return merged

    def _get_from_cache(self, name: str) -> Optional[ArtistIdentity]:
        """Get cached MusicBrainz artist identity."""
        if not self.cache:
            return None

        key = f"mb_artist:{name.lower()}"
        cached = self.cache.get(key)

        if not cached:
            return None

        try:
            return ArtistIdentity(
                mb_id=cached['mb_id'],
                canonical_name=cached['canonical_name'],
                aliases=cached['aliases'],
                artist_type=cached['artist_type'],
                sort_name=cached.get('sort_name'),
                disambiguation=cached.get('disambiguation'),
                confidence=cached.get('confidence', 1.0)
            )
        except (KeyError, TypeError) as exc:
            logger.warning(f"Invalid cached data for {name}: {exc}")
            return None

    def _store_in_cache(self, name: str, identity: ArtistIdentity) -> None:
        """Store MusicBrainz artist identity in cache."""
        if not self.cache:
            return

        key = f"mb_artist:{name.lower()}"
        self.cache.set(key, {
            'mb_id': identity.mb_id,
            'canonical_name': identity.canonical_name,
            'aliases': identity.aliases,
            'artist_type': identity.artist_type,
            'sort_name': identity.sort_name,
            'disambiguation': identity.disambiguation,
            'confidence': identity.confidence
        }, ttl=90 * 24 * 3600)  # Cache for 90 days
