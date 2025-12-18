from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import CandidateSourcePlugin
from ...daemon_types import ReleaseExample


class MusicBrainzCandidateSourcePlugin(CandidateSourcePlugin):
    name = "musicbrainz_candidate_source"

    def add(self, ctx: DirectoryContext) -> None:
        daemon = ctx.daemon
        if not getattr(daemon, "musicbrainz", None):
            return
        for pending in ctx.pending_results:
            if not pending.result:
                continue
            release_id = pending.meta.musicbrainz_release_id
            if not release_id:
                continue
            key = daemon._release_key("musicbrainz", release_id)
            ctx.release_scores[key] = max(
                ctx.release_scores.get(key, 0.0), float(pending.result.score)
            )
            release_data = daemon.musicbrainz.release_tracker.releases.get(release_id)
            ctx.release_examples[key] = ReleaseExample(
                provider="musicbrainz",
                title=(
                    release_data.album_title
                    if release_data and release_data.album_title
                    else pending.meta.album or ""
                )
                or "",
                artist=(
                    release_data.album_artist
                    if release_data and release_data.album_artist
                    else pending.meta.album_artist or pending.meta.artist or ""
                )
                or "",
                date=release_data.release_date if release_data else None,
                track_total=len(release_data.tracks)
                if release_data and release_data.tracks
                else None,
                disc_count=release_data.disc_count
                if release_data and release_data.disc_count
                else None,
                formats=list(release_data.formats) if release_data else [],
            )
