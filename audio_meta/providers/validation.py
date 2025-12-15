from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import musicbrainzngs

from ..config import ProviderSettings

logger = logging.getLogger(__name__)


def validate_providers(settings: ProviderSettings) -> None:
    errors: list[str] = []
    try:
        _validate_acoustid(settings.acoustid_api_key)
    except Exception as exc:  # pragma: no cover - network failure depends on env
        errors.append(f"AcoustID validation failed: {exc}")
    try:
        _validate_musicbrainz(settings.musicbrainz_useragent)
    except Exception as exc:  # pragma: no cover - network failure depends on env
        errors.append(f"MusicBrainz validation failed: {exc}")
    try:
        _validate_discogs(settings.discogs_token, settings.discogs_useragent)
    except Exception as exc:
        errors.append(f"Discogs validation failed: {exc}")
    if errors:
        message = "\n".join(errors)
        raise SystemExit(f"Provider validation failed:\n{message}")


def _validate_acoustid(api_key: str) -> None:
    params = urllib.parse.urlencode(
        {
            "client": api_key,
            "mbid": "5b11f4ce-a62d-471e-81fc-a69a8278c7da",
        }
    )
    url = f"https://api.acoustid.org/v2/track/list_by_mbid?{params}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"unable to reach AcoustID API: {exc}") from exc
    if payload.get("status") == "error":
        code = payload.get("error", {}).get("code")
        message = payload.get("error", {}).get("message", "unknown error")
        if code == 4:
            raise RuntimeError("invalid AcoustID API key")
        logger.debug("AcoustID preflight returned %s (%s)", code, message)


def _validate_musicbrainz(useragent: str) -> None:
    if not useragent or "example.com" in useragent:
        raise RuntimeError("musicbrainz_useragent must include a real contact (e.g. email or URL)")
    musicbrainzngs.set_useragent("audio-meta", "0.1", contact=useragent)
    try:
        musicbrainzngs.search_recordings(artist="Miles Davis", limit=1)
    except musicbrainzngs.ResponseError as exc:
        raise RuntimeError(f"MusicBrainz API call failed: {exc}") from exc


def _validate_discogs(token: Optional[str], useragent: str) -> None:
    if not token:
        return
    url = f"https://api.discogs.com/database/search?token={token}&type=release&per_page=1"
    req = urllib.request.Request(url, headers={"User-Agent": useragent})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Discogs token rejected") from exc
        raise RuntimeError(f"Discogs HTTP error {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"unable to reach Discogs API: {exc}") from exc
