from __future__ import annotations

import logging
from typing import Optional

from ..models import TrackMetadata
from ..providers.musicbrainz import LookupResult

logger = logging.getLogger(__name__)


def enrich_track_default(daemon, meta: TrackMetadata) -> Optional[LookupResult]:
    result = daemon.musicbrainz.enrich(meta)
    if result and daemon.discogs and daemon._needs_supplement(meta):
        try:
            supplement = daemon.discogs.supplement(meta)
            if supplement:
                result = LookupResult(meta, score=max(result.score, supplement.score))
        except Exception:
            logger.exception("Discogs supplement failed for %s", meta.path)
    if not result and daemon.discogs:
        try:
            result = daemon.discogs.enrich(meta)
        except Exception:
            logger.exception("Discogs lookup failed for %s", meta.path)
    return result

