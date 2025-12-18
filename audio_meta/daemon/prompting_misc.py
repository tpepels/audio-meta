from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..daemon_types import PendingResult
    from ..models import TrackMetadata
    from .core import AudioMetaDaemon


def confirm_classical_credits(
    daemon: "AudioMetaDaemon", directory: Path, metas: list["TrackMetadata"]
) -> bool:
    try:
        display = daemon._display_path(directory)
        stats = daemon._classical_credits_stats(metas)
        daemon.prompt_io.print(f"\nClassical credits check for {display}:")
        daemon.prompt_io.print(
            f"- Classical tracks: {stats['classical_tracks']}/{len(metas)}"
        )
        daemon.prompt_io.print(
            f"- Performer hint coverage: {stats['coverage']:.0%} ({stats['hinted_tracks']} hinted, {stats['missing_hints']} missing)"
        )
        if stats["consensus"] is not None:
            daemon.prompt_io.print(
                f"- Performer hint consensus: {float(stats['consensus']):.0%}"
            )
        top = list(stats["top_hints"])
        if top:
            daemon.prompt_io.print("- Top performer candidates:")
            for hint, count in top[:3]:
                daemon.prompt_io.print(f"  - {hint} ({count})")
        composer = next((m.composer for m in metas if m.composer), None)
        if composer:
            daemon.prompt_io.print(f"- Composer hint: {composer}")
        choice = daemon.prompt_io.input("Proceed anyway? [y/N]: ").strip().lower()
        return choice in {"y", "yes"}
    except Exception:
        return False


def prompt_on_unmatched_release(
    daemon: "AudioMetaDaemon",
    directory: Path,
    release_key: str,
    unmatched: list["PendingResult"],
) -> bool:
    provider, release_id = daemon._split_release_key(release_key)
    display_name = daemon._display_path(directory)
    sample_titles = []
    for pending in unmatched[:5]:
        title = pending.meta.title or pending.meta.path.name
        sample_titles.append(f"- {title}")
    daemon.prompt_io.print(
        f"\nOnly {len(unmatched)} file(s) in {display_name} failed to match release {provider}:{release_id}."
    )
    if sample_titles:
        daemon.prompt_io.print("Unmatched tracks:")
        for entry in sample_titles:
            daemon.prompt_io.print(f"  {entry}")
    choice = (
        daemon.prompt_io.input("Proceed with the matched tracks? [y/N] ")
        .strip()
        .lower()
    )
    return choice in {"y", "yes"}
