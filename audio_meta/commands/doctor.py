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

