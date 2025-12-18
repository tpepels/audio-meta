from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .audit import LibraryAuditor
from .cache import MetadataCache
from .config import Settings
from .daemon import AudioMetaDaemon
from .providers.discogs import DiscogsClient
from .providers.musicbrainz import MusicBrainzClient
from .scanner import LibraryScanner
import logging


@dataclass
class AudioMetaApp:
    settings: Settings
    cache: MetadataCache
    scanner: LibraryScanner
    musicbrainz: MusicBrainzClient
    discogs: DiscogsClient | None = None
    _daemon: AudioMetaDaemon | None = None
    _auditor: LibraryAuditor | None = None

    @classmethod
    def create(cls, settings: Settings) -> "AudioMetaApp":
        cache = MetadataCache(settings.daemon.cache_path)
        scanner = LibraryScanner(settings.library)
        musicbrainz = MusicBrainzClient(settings.providers, cache=cache)
        discogs: DiscogsClient | None = None
        if settings.providers.discogs_token:
            try:
                discogs = DiscogsClient(settings.providers, cache=cache)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Failed to initialise Discogs client: %s", exc
                )
        return cls(
            settings=settings,
            cache=cache,
            scanner=scanner,
            musicbrainz=musicbrainz,
            discogs=discogs,
        )

    def get_daemon(
        self,
        *,
        dry_run_output: Optional[Path] = None,
        interactive: bool = False,
        release_cache_enabled: bool = True,
    ) -> AudioMetaDaemon:
        self._daemon = AudioMetaDaemon(
            self.settings,
            cache=self.cache,
            scanner=self.scanner,
            musicbrainz=self.musicbrainz,
            discogs=self.discogs,
            dry_run_output=dry_run_output,
            interactive=interactive,
            release_cache_enabled=release_cache_enabled,
        )
        return self._daemon

    def get_auditor(self) -> LibraryAuditor:
        if self._auditor is None:
            self._auditor = LibraryAuditor(
                self.settings, cache=self.cache, musicbrainz=self.musicbrainz
            )
        return self._auditor

    def close(self) -> None:
        self.cache.close()
