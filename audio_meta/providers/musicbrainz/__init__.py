"""
MusicBrainz provider package.

This package contains MusicBrainz integrations:
- Identity resolution (artist name canonicalization)
- Release matching (album identification)
- Track metadata enrichment
"""

from __future__ import annotations

# Re-export LookupResult for backward compatibility
# The main MusicBrainz client is in audio_meta.providers.musicbrainz (the .py file)
# This package (musicbrainz/) is for new modular components like identity_resolver
try:
    from ..musicbrainz import LookupResult
    __all__ = ["LookupResult"]
except ImportError:
    __all__ = []
