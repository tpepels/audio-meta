from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import TrackAssignmentPlugin


class DefaultTrackAssignmentPlugin(TrackAssignmentPlugin):
    name = "default_track_assignment"

    def assign(self, ctx: DirectoryContext, force: bool = False) -> bool:
        provider, rid = ctx.split_best_release()
        if not provider or not rid:
            return False
        services = ctx.daemon.services
        if provider == "musicbrainz":
            return bool(
                services.apply_musicbrainz_release_selection(
                    ctx.directory, rid, ctx.pending_results, force=force
                )
            )
        if provider == "discogs":
            details = ctx.discogs_release_details or ctx.discogs_details.get(
                ctx.best_release_key or ""
            )
            if not details:
                return False
            services.apply_discogs_release_details(ctx.pending_results, details)
            return True
        return False
