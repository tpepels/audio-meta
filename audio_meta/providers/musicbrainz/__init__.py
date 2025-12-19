"""
MusicBrainz provider package.

This package contains MusicBrainz integrations:
- Identity resolution (artist name canonicalization)
- Release matching (album identification)
- Track metadata enrichment
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import TrackMetadata

# Re-export LookupResult for backward compatibility
# This used to be in the old musicbrainz.py file (now musicbrainz_client.py),
# but we define it here to avoid circular imports
@dataclass(slots=True)
class LookupResult:
    """Result from a MusicBrainz track lookup."""
    track: "TrackMetadata"
    score: float

# Re-export all classes from musicbrainz_client.py for backward compatibility
# The old musicbrainz.py was renamed to musicbrainz_client.py to avoid package/module name conflict
from ..musicbrainz_client import (
    MusicBrainzClient,
    ReleaseData,
    ReleaseMatch,
    ReleaseTrack,
    ReleaseTracker,
)

__all__ = [
    "LookupResult",
    "MusicBrainzClient",
    "ReleaseData",
    "ReleaseMatch",
    "ReleaseTrack",
    "ReleaseTracker",
]
