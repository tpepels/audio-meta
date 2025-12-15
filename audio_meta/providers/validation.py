from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

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
    if errors:
        message = "\n".join(errors)
        raise SystemExit(f"Provider validation failed:\n{message}")


def _validate_acoustid(api_key: str) -> None:
    params = urllib.parse.urlencode(
        {
            "client": api_key,
            "duration": 1,
            "fingerprint": "AQAAAAAA",
        }
    )
    url = f"https://api.acoustid.org/v2/lookup?{params}"
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
