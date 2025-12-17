from __future__ import annotations

from typing import Optional

from ..contexts import TrackEnrichmentContext
from ..protocols import TrackEnricherPlugin


class DefaultTrackEnricherPlugin(TrackEnricherPlugin):
    name = "default_track_enrichment"

    def enrich(self, ctx: TrackEnrichmentContext) -> Optional[object]:
        return ctx.daemon._enrich_track_default(ctx.meta)

