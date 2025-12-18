from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..app import AudioMetaApp
from ..config import Settings
from ..pipeline import ProcessingPipeline
from ..providers.validation import validate_providers
from .output import disabled, enabled, error, ok as ok_line, skipped, warning


@dataclass(slots=True)
class DoctorReport:
    ok: bool
    checks: list[str]


def _recent_provider_health_lines(app: AudioMetaApp) -> list[str]:
    cache = getattr(app, "cache", None)
    if cache is None:
        return []
    try:
        events = cache.list_audit_events(event="scan_complete", limit=1)
    except Exception:
        return []
    if not events:
        return []
    payload = events[0].get("payload") or {}
    warn_log_path = payload.get("warning_log_path")
    if not warn_log_path:
        return []
    path = Path(str(warn_log_path))
    if not path.exists():
        return [warning("Providers (recent warnings)", f"missing log {path}")]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [warning("Providers (recent warnings)", f"unreadable log {path}")]
    # Keep this conservative: just detect common “provider offline” symptoms.
    needles = (
        "Temporary failure in name resolution",
        "NameResolutionError",
        "socket.gaierror",
        "NetworkError",
        "HTTPConnectionPool",
        "HTTPSConnectionPool",
    )
    hits = [n for n in needles if n in text]
    if hits:
        return [
            warning(
                "Providers (recent warnings)",
                "possible provider/DNS outage (see warnings log)",
            )
        ]
    return [ok_line("Providers (recent warnings)", "no obvious network errors")]


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
        checks.append(ok_line("Cache", str(cache_path)))

        roots = [root.resolve() for root in settings.library.roots]
        missing = [str(root) for root in roots if not root.exists()]
        if missing:
            ok = False
            checks.append(error("Library roots", f"missing: {', '.join(missing)}"))
        else:
            checks.append(ok_line("Library roots", f"{len(roots)} root(s)"))

        if settings.providers.discogs_token:
            checks.append(enabled("Discogs"))
        else:
            checks.append(disabled("Discogs", "set providers.discogs_token"))

        deferred = app.cache.list_deferred_prompts()
        if deferred:
            reasons: dict[str, int] = {}
            for _path, reason in deferred[:500]:
                reasons[reason] = reasons.get(reason, 0) + 1
            top = ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())
            )
            count = len(deferred)
            if count >= 50:
                checks.append(
                    warning(
                        "Deferred prompts",
                        f"{count} pending (run `audio-meta deferred`; {top})",
                    )
                )
            else:
                checks.append(
                    ok_line(
                        "Deferred prompts",
                        f"{count} pending (run `audio-meta deferred`; {top})",
                    )
                )
        else:
            checks.append(ok_line("Deferred prompts", "0 pending"))

        if getattr(settings.organizer, "enabled", False):
            target_root = getattr(settings.organizer, "target_root", None)
            if target_root is None:
                ok = False
                checks.append(
                    error("Organizer", "enabled but target_root not set")
                )
            elif not Path(target_root).exists():
                ok = False
                checks.append(
                    error("Organizer", f"target_root missing: {target_root}")
                )
            else:
                checks.append(enabled("Organizer", f"target_root={target_root}"))
            archive_root = getattr(settings.organizer, "archive_root", None)
            if archive_root is None:
                checks.append(
                    warning(
                        "Organizer archive",
                        "archive_root not set; archive action may be unavailable",
                    )
                )
            else:
                checks.append(ok_line("Organizer archive", f"archive_root={archive_root}"))
        else:
            checks.append(disabled("Organizer"))

        process_deferred = bool(
            getattr(settings.daemon, "process_deferred_prompts_at_end", True)
        )
        checks.append(
            enabled("Deferred processing at end")
            if process_deferred
            else disabled("Deferred processing at end")
        )

        try:
            ProcessingPipeline(
                disabled_plugins=set(settings.daemon.pipeline_disable),
                plugin_order=dict(settings.daemon.pipeline_order),
            )
            checks.append(ok_line("Pipeline"))
        except Exception as exc:
            ok = False
            checks.append(error("Pipeline", str(exc)))

        checks.extend(_recent_provider_health_lines(app))

        if validate_providers_online:
            validate_providers(settings.providers)
            checks.append(ok_line("Providers (network)"))
        else:
            checks.append(skipped("Providers (network)", "pass --providers"))
    finally:
        app.close()

    return DoctorReport(ok=ok, checks=checks)
