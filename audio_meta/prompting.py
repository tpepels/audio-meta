from __future__ import annotations

from typing import Optional


def release_url(provider: str, release_id: str) -> Optional[str]:
    if not provider or not release_id:
        return None
    if provider == "musicbrainz":
        return f"https://musicbrainz.org/release/{release_id}"
    if provider == "discogs":
        if str(release_id).isdigit():
            return f"https://www.discogs.com/release/{release_id}"
        return None
    return None


def manual_release_choice_help(*, discogs_enabled: bool) -> str:
    if discogs_enabled:
        return "  mb:<release-id> or dg:<release-id> to enter an ID manually"
    return "  mb:<release-id> to enter an ID manually"


def invalid_release_choice_message(*, discogs_enabled: bool) -> str:
    if discogs_enabled:
        return "Invalid selection; enter a number or mb:/dg: identifier."
    return "Invalid selection; enter a number or mb: identifier."
