from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..app import AudioMetaApp
from ..config import Settings
from ..pipeline import ProcessingPipeline
from ..providers.validation import validate_providers


@dataclass(slots=True)
class DoctorReport:
    ok: bool
    checks: list[str]


def run(
    settings: Settings,
    *,
    validate_providers_online: bool = False,
) -> DoctorReport:
    checks: list[str] = []
    ok = True

    app = AudioMetaApp.create(settings)
    try:
        cache_path = Path(settings.daemon.cache_path)
        checks.append(f"Cache: OK ({cache_path})")

        roots = [root.resolve() for root in settings.library.roots]
        missing = [str(root) for root in roots if not root.exists()]
        if missing:
            ok = False
            checks.append(f"Library roots: MISSING ({', '.join(missing)})")
        else:
            checks.append(f"Library roots: OK ({len(roots)})")

        if settings.providers.discogs_token:
            checks.append("Discogs: ENABLED")
        else:
            checks.append("Discogs: DISABLED (set providers.discogs_token)")

        deferred = app.cache.list_deferred_prompts()
        if deferred:
            reasons: dict[str, int] = {}
            for _path, reason in deferred[:500]:
                reasons[reason] = reasons.get(reason, 0) + 1
            top = ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())
            )
            checks.append(
                f"Deferred prompts: {len(deferred)} pending (run `audio-meta deferred`; {top})"
            )
        else:
            checks.append("Deferred prompts: 0 pending")

        if getattr(settings.organizer, "enabled", False):
            archive_root = getattr(settings.organizer, "archive_root", None)
            if archive_root is None:
                checks.append(
                    "Organizer: ENABLED (archive_root not set; archive action may be unavailable)"
                )
            else:
                checks.append("Organizer: ENABLED")
        else:
            checks.append("Organizer: DISABLED")

        process_deferred = bool(
            getattr(settings.daemon, "process_deferred_prompts_at_end", True)
        )
        checks.append(
            "Deferred processing at end: "
            + ("ENABLED" if process_deferred else "DISABLED")
        )

        try:
            ProcessingPipeline(
                disabled_plugins=set(settings.daemon.pipeline_disable),
                plugin_order=dict(settings.daemon.pipeline_order),
            )
            checks.append("Pipeline: OK")
        except Exception as exc:
            ok = False
            checks.append(f"Pipeline: ERROR ({exc})")

        if validate_providers_online:
            validate_providers(settings.providers)
            checks.append("Providers (network): OK")
        else:
            checks.append("Providers (network): SKIPPED (pass --providers)")
    finally:
        app.close()

    return DoctorReport(ok=ok, checks=checks)
