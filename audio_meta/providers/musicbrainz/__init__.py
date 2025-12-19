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
# This used to be in the old musicbrainz.py file, but now we define it here
# to avoid circular imports since both the old .py file and this new package exist
@dataclass(slots=True)
class LookupResult:
    """Result from a MusicBrainz track lookup."""
    track: "TrackMetadata"
    score: float

__all__ = ["LookupResult"]
