from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import CandidateSourcePlugin
from ...daemon_types import ReleaseExample


class MusicBrainzCandidateSourcePlugin(CandidateSourcePlugin):
    name = "musicbrainz_candidate_source"

    def add(self, ctx: DirectoryContext) -> None:
        daemon = ctx.daemon
        services = daemon.services
        if not getattr(daemon, "musicbrainz", None):
            return
        votes: dict[str, list[float]] = {}
        best_pending = {}
        for pending in ctx.pending_results:
            if not pending.result:
                continue
            release_id = pending.meta.musicbrainz_release_id
            if not release_id:
                continue
            votes.setdefault(release_id, []).append(float(pending.result.score))
            current_best = best_pending.get(release_id)
            if (
                current_best is None
                or float(current_best.result.score) < float(pending.result.score)
            ):
                best_pending[release_id] = pending

        if not votes:
            return

        dir_track_count = int(ctx.dir_track_count or 0)
        if not dir_track_count:
            dir_track_count = len(ctx.files) or len(ctx.pending_results) or 0
        denom = max(2, min(6, dir_track_count or 2))

        for release_id, scores in votes.items():
            support = len(scores)
            avg_score = sum(scores) / support
            support_factor = 1.0 if ctx.is_singleton else min(1.0, support / denom)
            effective_score = avg_score * support_factor

            key = services.release_key("musicbrainz", release_id)
            ctx.release_scores[key] = max(
                ctx.release_scores.get(key, 0.0), float(effective_score)
            )

            pending = best_pending.get(release_id)
            release_data = daemon.musicbrainz.release_tracker.releases.get(release_id)
            meta = getattr(pending, "meta", None)
            ctx.release_examples[key] = ReleaseExample(
                provider="musicbrainz",
                title=(
                    release_data.album_title
                    if release_data and release_data.album_title
                    else (meta.album if meta else "") or ""
                ),
                artist=(
                    release_data.album_artist
                    if release_data and release_data.album_artist
                    else (
                        (meta.album_artist if meta else None)
                        or (meta.artist if meta else None)
                        or ""
                    )
                ),
                date=release_data.release_date if release_data else None,
                track_total=len(release_data.tracks)
                if release_data and release_data.tracks
                else None,
                disc_count=release_data.disc_count
                if release_data and release_data.disc_count
                else None,
                formats=list(release_data.formats) if release_data else [],
            )
