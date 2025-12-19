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

# Re-export all classes from the old musicbrainz.py module for backward compatibility
# Import from the parent musicbrainz.py file (not this package)
# We use a late import to avoid issues during module initialization
def _import_legacy_classes():
    """Import classes from the legacy musicbrainz.py file."""
    import importlib.util
    from pathlib import Path

    # Get the path to musicbrainz.py (the file, not this package)
    mb_file = Path(__file__).parent.parent / "musicbrainz.py"

    if not mb_file.exists():
        return None

    spec = importlib.util.spec_from_file_location("_mb_legacy", mb_file)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None

# Import and re-export legacy classes
_legacy = _import_legacy_classes()
if _legacy:
    MusicBrainzClient = _legacy.MusicBrainzClient
    ReleaseData = _legacy.ReleaseData
    ReleaseMatch = _legacy.ReleaseMatch
    ReleaseTrack = _legacy.ReleaseTrack
    ReleaseTracker = _legacy.ReleaseTracker

    __all__ = [
        "LookupResult",
        "MusicBrainzClient",
        "ReleaseData",
        "ReleaseMatch",
        "ReleaseTrack",
        "ReleaseTracker",
    ]
else:
    __all__ = ["LookupResult"]
