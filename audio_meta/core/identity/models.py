"""
Domain models for identity and canonicalization.

These are pure data models with no dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IdentityCluster:
    """
    A cluster of name variants that refer to the same entity.

    Example:
        Cluster for Beethoven:
        - canonical: "Ludwig van Beethoven"
        - canonical_id: "composer::ludwigvonbeethoven"
        - variants: {"Beethoven", "Ludwig van Beethoven", "L. van Beethoven"}
        - occurrences: 150
    """
    canonical: str
    """The chosen canonical form for this identity"""

    canonical_id: str
    """Unique identifier: category::normalized_token"""

    variants: set[str]
    """All observed variants for this identity"""

    occurrences: int
    """Total number of times this identity appeared across all variants"""


@dataclass
class IdentityScanResult:
    """
    Results from scanning a library for identity information.

    Contains clustered identities for artists, composers, etc.
    """
    artists: dict[str, IdentityCluster] = field(default_factory=dict)
    """Clustered artist identities, keyed by normalized token"""

    composers: dict[str, IdentityCluster] = field(default_factory=dict)
    """Clustered composer identities, keyed by normalized token"""

    album_artists: dict[str, IdentityCluster] = field(default_factory=dict)
    """Clustered album artist identities, keyed by normalized token"""

    conductors: dict[str, IdentityCluster] = field(default_factory=dict)
    """Clustered conductor identities, keyed by normalized token"""

    performers: dict[str, IdentityCluster] = field(default_factory=dict)
    """Clustered performer identities, keyed by normalized token"""

    files_scanned: int = 0
    """Number of audio files scanned"""

    def total_clusters(self) -> int:
        """Total number of identity clusters across all categories."""
        return (
            len(self.artists)
            + len(self.composers)
            + len(self.album_artists)
            + len(self.conductors)
            + len(self.performers)
        )

    def get_category_clusters(self, category: str) -> dict[str, IdentityCluster]:
        """Get clusters for a specific category."""
        return getattr(self, category, {})


@dataclass
class MatchResult:
    """
    Result of matching two name variants.

    Contains information about whether they match and why.
    """
    matches: bool
    """Whether the two variants match"""

    strategy: Optional[str] = None
    """The matching strategy that succeeded (exact, substring, initials, fuzzy)"""

    confidence: float = 1.0
    """Confidence score (0.0 to 1.0)"""

    details: Optional[str] = None
    """Human-readable explanation of the match"""
