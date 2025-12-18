from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TALB, TPE1, TPE2, TCON, TCOM, COMM, TRCK, TPOS
from mutagen.mp4 import MP4
from mutagen.flac import FLAC

from .models import TrackMetadata

logger = logging.getLogger(__name__)


class TagWriter:
    """Handles reading/writing metadata across the most common tagging formats."""

    SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}

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

    def has_changes(self, meta: TrackMetadata) -> bool:
        return bool(self.diff(meta))

    def diff(self, meta: TrackMetadata) -> Dict[str, Dict[str, Optional[str]]]:
        ext = meta.path.suffix.lower()
        if ext not in self.SUPPORTED_EXTS:
            return {}
        current = self._read_tags(meta, ext)
        desired = self._desired_map(meta)
        changes: Dict[str, Dict[str, Optional[str]]] = {}
        for key, expected in desired.items():
            current_value = None if current is None else current.get(key)
            if expected is not None and self._normalize(current_value) != self._normalize(expected):
                changes[key] = {
                    "old": current_value,
                    "new": expected,
                }
        return changes

    def read_existing_tags(self, meta: TrackMetadata) -> Optional[Dict[str, Optional[str]]]:
        ext = meta.path.suffix.lower()
        if ext not in self.SUPPORTED_EXTS:
            return None
        return self._read_tags(meta, ext)

    def _desired_map(self, meta: TrackMetadata) -> Dict[str, Optional[str]]:
        mapping = {
            "title": meta.title,
            "album": meta.album,
            "artist": meta.artist,
            "album_artist": meta.album_artist,
            "composer": meta.composer,
            "genre": meta.genre,
            "work": meta.work,
            "movement": meta.movement,
        }
        return {k: v for k, v in mapping.items() if v is not None}

    def _read_tags(self, meta: TrackMetadata, ext: str) -> Optional[Dict[str, Optional[str]]]:
        try:
            if ext == ".mp3":
                tags = ID3(meta.path)
                return {
                    "title": self._id3_text(tags, "TIT2"),
                    "album": self._id3_text(tags, "TALB"),
                    "artist": self._id3_text(tags, "TPE1"),
                    "album_artist": self._id3_text(tags, "TPE2"),
                    "composer": self._id3_text(tags, "TCOM"),
                    "genre": self._id3_text(tags, "TCON"),
                    "work": self._id3_text(tags, "TIT1"),
                    "movement": self._id3_text(tags, "MVNM"),
                    "tracknumber": self._id3_text(tags, "TRCK"),
                    "discnumber": self._id3_text(tags, "TPOS"),
                    "date": self._id3_text(tags, "TDRC") or self._id3_text(tags, "TYER"),
                }
            if ext == ".flac":
                audio = FLAC(meta.path)
                return {
                    "title": audio.get("TITLE", [None])[0],
                    "album": audio.get("ALBUM", [None])[0],
                    "artist": audio.get("ARTIST", [None])[0],
                    "album_artist": audio.get("ALBUMARTIST", [None])[0],
                    "composer": audio.get("COMPOSER", [None])[0],
                    "genre": audio.get("GENRE", [None])[0],
                    "work": audio.get("WORK", [None])[0],
                    "movement": audio.get("MOVEMENT", [None])[0],
                    "tracknumber": audio.get("TRACKNUMBER", [None])[0],
                    "discnumber": audio.get("DISCNUMBER", [None])[0],
                    "date": audio.get("DATE", [None])[0] or audio.get("YEAR", [None])[0],
                }
            if ext == ".m4a":
                audio = MP4(meta.path)
                track_info = audio.get("trkn")
                track_number = None
                if track_info and isinstance(track_info, list) and track_info:
                    first = track_info[0]
                    if isinstance(first, (tuple, list)) and first:
                        track_number = str(first[0])
                disc_info = audio.get("disk")
                disc_number = None
                if disc_info and isinstance(disc_info, list) and disc_info:
                    first_disc = disc_info[0]
                    if isinstance(first_disc, (tuple, list)) and first_disc:
                        disc_number = str(first_disc[0])
                return {
                    "title": self._mp4_text(audio, "\xa9nam"),
                    "album": self._mp4_text(audio, "\xa9alb"),
                    "artist": self._mp4_text(audio, "\xa9ART"),
                    "album_artist": self._mp4_text(audio, "aART"),
                    "composer": self._mp4_text(audio, "----:com.apple.iTunes:COMPOSER"),
                    "genre": self._mp4_text(audio, "\xa9gen"),
                    "work": self._mp4_text(audio, "----:com.apple.iTunes:WORK"),
                    "movement": self._mp4_text(audio, "----:com.apple.iTunes:MOVEMENT"),
                    "tracknumber": track_number,
                    "discnumber": disc_number,
                    "date": self._mp4_text(audio, "\xa9day"),
                }
        except Exception as exc:  # pragma: no cover - depends on local files
            logger.debug("Failed to read tags for %s: %s", meta.path, exc)
            return None
        return None

    def _id3_text(self, tags: ID3, frame_id: str) -> Optional[str]:
        frame = tags.getall(frame_id)
        if not frame:
            return None
        return frame[0].text[0] if frame[0].text else None

    def _mp4_text(self, audio: MP4, key: str) -> Optional[str]:
        value = audio.get(key)
        if not value:
            return None
        first = value[0]
        if isinstance(first, bytes):
            return first.decode("utf-8", errors="replace")
        return str(first)

    @staticmethod
    def _normalize(value: Optional[str]) -> str:
        if value is None:
            return ""
        return value.strip()

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
        extra = self._stringify_extra(meta.extra)
        track_number = extra.pop("TRACKNUMBER", None)
        disc_number = extra.pop("DISCNUMBER", None)
        if track_number:
            tags.setall("TRCK", [TRCK(encoding=3, text=track_number)])
        if disc_number:
            tags.setall("TPOS", [TPOS(encoding=3, text=disc_number)])
        for key, value in extra.items():
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
            "----:com.apple.iTunes:WORK": meta.work,
            "----:com.apple.iTunes:MOVEMENT": meta.movement,
        }
        for key, value in mapping.items():
            if value:
                self._set_mp4_value(audio, key, value)
        extra = self._stringify_extra(meta.extra)
        track_number = extra.pop("TRACKNUMBER", None)
        disc_number = extra.pop("DISCNUMBER", None)
        if track_number and track_number.isdigit():
            audio["trkn"] = [(int(track_number), 0)]
        if disc_number and disc_number.isdigit():
            audio["disk"] = [(int(disc_number), 0)]
        for key, value in extra.items():
            self._set_mp4_value(audio, f"----:com.audio-meta:{key}", value, freeform=True)
        audio.save()

    def _write_vorbis_comments(self, audio: FLAC, mapping: Dict[str, str | None], extra: Dict[str, Any]) -> None:
        for key, value in mapping.items():
            if value:
                audio[key] = value
        for key, value in extra.items():
            if value is None:
                continue
            audio[key] = value if isinstance(value, str) else str(value)

    def _set_frame(self, tags: ID3, frame_cls, value: str | None, desc: str = "") -> None:
        if value:
            tags.setall(frame_cls.__name__, [frame_cls(encoding=3, text=value, desc=desc)])

    def _set_mp4_value(self, audio: MP4, key: str, value: str, freeform: bool = False) -> None:
        if freeform or key.startswith("----:"):
            payload = value.encode("utf-8") if isinstance(value, str) else value
            audio[key] = [payload]
        else:
            audio[key] = [value]

    @staticmethod
    def _stringify_extra(extra: Dict[str, Any]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for key, value in (extra or {}).items():
            if value is None:
                continue
            result[str(key)] = value if isinstance(value, str) else str(value)
        return result
