from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

from mutagen import File
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TALB, TPE1, TPE2, TCON, TCOM, COMM
from mutagen.mp4 import MP4
from mutagen.flac import FLAC

from .models import TrackMetadata, ProcessingError

logger = logging.getLogger(__name__)


class TagWriter:
    """Handles reading/writing metadata across the most common tagging formats."""

    def apply(self, meta: TrackMetadata) -> None:
        handlers = {
            ".mp3": self._apply_mp3,
            ".flac": self._apply_flac,
            ".m4a": self._apply_mp4,
        }
        ext = meta.path.suffix.lower()
        handler = handlers.get(ext)
        if not handler:
            logger.debug("Skipping unsupported extension %s", meta.path)
            return
        handler(meta)

    def _apply_mp3(self, meta: TrackMetadata) -> None:
        try:
            tags = ID3(meta.path)
        except ID3NoHeaderError:
            tags = ID3()
        self._set_frame(tags, TIT2, meta.title)
        self._set_frame(tags, TALB, meta.album)
        self._set_frame(tags, TPE1, meta.artist)
        self._set_frame(tags, TPE2, meta.album_artist)
        self._set_frame(tags, TCON, meta.genre)
        self._set_frame(tags, TCOM, meta.composer)
        for key, value in meta.extra.items():
            self._set_frame(tags, COMM, value, desc=key)
        tags.save(meta.path)

    def _apply_flac(self, meta: TrackMetadata) -> None:
        audio = FLAC(meta.path)
        mapping: Dict[str, str | None] = {
            "TITLE": meta.title,
            "ARTIST": meta.artist,
            "ALBUM": meta.album,
            "ALBUMARTIST": meta.album_artist,
            "COMPOSER": meta.composer,
            "GENRE": meta.genre,
            "WORK": meta.work,
            "MOVEMENT": meta.movement,
        }
        self._write_vorbis_comments(audio, mapping, meta.extra)
        audio.save()

    def _apply_mp4(self, meta: TrackMetadata) -> None:
        audio = MP4(meta.path)
        mapping = {
            "\xa9nam": meta.title,
            "\xa9alb": meta.album,
            "\xa9ART": meta.artist,
            "aART": meta.album_artist,
            "\xa9gen": meta.genre,
            "----:com.apple.iTunes:COMPOSER": meta.composer,
        }
        for key, value in mapping.items():
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
        for key, value in meta.extra.items():
            audio[f"----:com.audio-meta:{key}"] = [value.encode("utf-8")]
        audio.save()

    def _write_vorbis_comments(self, audio: FLAC, mapping: Dict[str, str | None], extra: Dict[str, str]) -> None:
        for key, value in mapping.items():
            if value:
                audio[key] = value
            elif key in audio:
                del audio[key]
        for key, value in extra.items():
            audio[key] = value

    def _set_frame(self, tags: ID3, frame_cls, value: str | None, desc: str = "") -> None:
        if value:
            tags.setall(frame_cls.__name__, [frame_cls(encoding=3, text=value, desc=desc)])
        else:
            tags.delall(frame_cls.__name__)
