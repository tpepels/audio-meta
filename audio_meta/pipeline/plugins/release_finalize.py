from __future__ import annotations

import logging

from ..contexts import DirectoryContext
from ..protocols import ReleaseFinalizePlugin
from ..types import ReleaseFinalizeOutcome
from ...release_selection import ReleaseDecision

logger = logging.getLogger(__name__)


class DefaultReleaseFinalizePlugin(ReleaseFinalizePlugin):
    name = "default_release_finalize"

    def finalize(
        self, ctx: DirectoryContext, decision: ReleaseDecision
    ) -> ReleaseFinalizeOutcome | None:
        daemon = ctx.daemon
        services = daemon.services
        best_release_id = decision.best_release_id
        best_score = decision.best_score

        discogs_release_details = (
            decision.discogs_release_details or ctx.discogs_release_details
        )
        release_summary_printed = decision.release_summary_printed

        applied_provider: str | None = None
        applied_release_plain_id: str | None = None
        album_name = ""
        album_artist = ""

        if best_release_id:
            best_provider, best_release_plain_id = services.split_release_key(
                best_release_id
            )
            applied_provider = best_provider
            applied_release_plain_id = best_release_plain_id

            example = ctx.release_examples.get(best_release_id)
            if example:
                if example.title:
                    album_name = example.title
                if example.artist:
                    album_artist = example.artist

            if best_provider == "musicbrainz":
                release_ref = services.fetch_musicbrainz_release(best_release_plain_id)
                if not release_ref:
                    services.record_skip(
                        ctx.directory,
                        f"MusicBrainz release {best_release_plain_id} unavailable",
                    )
                    logger.warning(
                        "MusicBrainz release %s unavailable for %s",
                        best_release_plain_id,
                        ctx.directory,
                    )
                    return ReleaseFinalizeOutcome(
                        provider=applied_provider,
                        release_id=applied_release_plain_id,
                        album_name=album_name,
                        album_artist=album_artist,
                        discogs_release_details=None,
                        release_summary_printed=release_summary_printed,
                    )
                if not album_name:
                    album_name = release_ref.album_title or ""
                if not album_artist:
                    album_artist = release_ref.album_artist or ""
                if not release_summary_printed:
                    services.print_release_selection_summary(
                        ctx.directory,
                        "musicbrainz",
                        best_release_plain_id,
                        album_name,
                        album_artist,
                        len(release_ref.tracks) if release_ref.tracks else None,
                        release_ref.disc_count,
                        ctx.pending_results,
                    )
                    release_summary_printed = True
                services.persist_directory_release(
                    ctx.directory,
                    "musicbrainz",
                    best_release_plain_id,
                    best_score,
                    artist_hint=album_artist,
                    album_hint=album_name,
                )
            else:
                details = discogs_release_details or ctx.discogs_details.get(
                    best_release_id
                )
                if not details and getattr(daemon, "discogs", None):
                    try:
                        details = daemon.discogs.get_release(int(best_release_plain_id))
                    except Exception as exc:  # pragma: no cover
                        logger.warning(
                            "Failed to load Discogs release %s: %s",
                            best_release_plain_id,
                            exc,
                        )
                        details = None
                    if details:
                        ctx.discogs_details[best_release_id] = details
                if not details:
                    services.record_skip(
                        ctx.directory,
                        f"Discogs release {best_release_plain_id} unavailable",
                    )
                    logger.warning(
                        "Discogs release %s unavailable for %s",
                        best_release_plain_id,
                        ctx.directory,
                    )
                    return ReleaseFinalizeOutcome(
                        provider=applied_provider,
                        release_id=applied_release_plain_id,
                        album_name=album_name,
                        album_artist=album_artist,
                        discogs_release_details=None,
                        release_summary_printed=release_summary_printed,
                    )
                discogs_release_details = details
                ctx.discogs_release_details = details
                services.apply_discogs_release_details(ctx.pending_results, details)
                if not album_name:
                    album_name = details.get("title") or ""
                discogs_artist = services.discogs_release_artist(details)
                if discogs_artist and not album_artist:
                    album_artist = discogs_artist
                if not release_summary_printed:
                    services.print_release_selection_summary(
                        ctx.directory,
                        "discogs",
                        best_release_plain_id,
                        album_name,
                        album_artist,
                        example.track_total if example else None,
                        example.disc_count if example else None,
                        ctx.pending_results,
                    )
                    release_summary_printed = True
                services.persist_directory_release(
                    ctx.directory,
                    "discogs",
                    best_release_plain_id,
                    best_score,
                    artist_hint=album_artist,
                    album_hint=album_name,
                )

        return ReleaseFinalizeOutcome(
            provider=applied_provider,
            release_id=applied_release_plain_id,
            album_name=album_name,
            album_artist=album_artist,
            discogs_release_details=discogs_release_details,
            release_summary_printed=release_summary_printed,
        )
