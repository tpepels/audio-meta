from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import DirectoryInitializerPlugin
from ...daemon_types import ReleaseExample


class DefaultDirectoryInitializerPlugin(DirectoryInitializerPlugin):
    name = "default_directory_initializer"

    def initialize(self, ctx: DirectoryContext) -> None:
        daemon = ctx.daemon
        services = daemon.services
        if not getattr(daemon, "release_cache_enabled", True):
            return
        cached_release_entry = daemon._cached_release_for_directory(ctx.directory)
        if not cached_release_entry and ctx.hash_release_entry:
            cached_release_entry = ctx.hash_release_entry
        if not cached_release_entry:
            return
        provider, cached_release_id, cached_score = cached_release_entry
        if provider == "musicbrainz" and getattr(daemon, "musicbrainz", None):
            release_data = services.fetch_musicbrainz_release(cached_release_id)
            if not release_data:
                return
            key = services.release_key("musicbrainz", cached_release_id)
            ctx.release_examples[key] = ReleaseExample(
                provider="musicbrainz",
                title=release_data.album_title or "",
                artist=release_data.album_artist or "",
                date=release_data.release_date,
                track_total=len(release_data.tracks) if release_data.tracks else None,
                disc_count=release_data.disc_count or None,
                formats=list(release_data.formats),
            )
            ctx.release_scores[key] = max(
                ctx.release_scores.get(key, 0.0), float(cached_score or 1.0)
            )
            return
        if provider == "discogs" and getattr(daemon, "discogs", None):
            details = daemon.discogs.get_release(int(cached_release_id))
            if not details:
                return
            key = services.release_key("discogs", cached_release_id)
            ctx.discogs_details[key] = details
            ctx.discogs_release_details = details
            ctx.release_examples[key] = ReleaseExample(
                provider="discogs",
                title=details.get("title") or "",
                artist=services.discogs_release_artist(details) or "",
                date=str(details.get("year") or ""),
                track_total=len(details.get("tracklist") or []),
                disc_count=details.get("disc_count"),
                formats=details.get("formats") or [],
            )
            ctx.release_scores[key] = max(
                ctx.release_scores.get(key, 0.0), float(cached_score or 1.0)
            )
