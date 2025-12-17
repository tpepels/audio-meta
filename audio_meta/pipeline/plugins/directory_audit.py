from __future__ import annotations

import logging
from collections import Counter

from ..contexts import DirectoryContext
from ..protocols import DirectoryDiagnosticsPlugin

logger = logging.getLogger(__name__)


class DirectoryAuditPlugin(DirectoryDiagnosticsPlugin):
    name = "directory_audit"

    def run(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        daemon = ctx.daemon
        cache = getattr(daemon, "cache", None)
        if cache is None:
            return
        skipped_tracks = ctx.diagnostics.get("skipped_tracks") if isinstance(ctx.diagnostics, dict) else None
        skipped_track_reasons: dict[str, int] = {}
        if isinstance(skipped_tracks, list):
            reasons = [r.get("reason") for r in skipped_tracks if isinstance(r, dict)]
            skipped_track_reasons = dict(Counter([r for r in reasons if r]))
        try:
            cache.append_audit_event(
                "directory_complete",
                {
                    "directory": str(ctx.directory),
                    "is_singleton": bool(ctx.is_singleton),
                    "release_key": ctx.best_release_key,
                    "applied_provider": ctx.applied_provider,
                    "applied_release_id": ctx.applied_release_id,
                    "planned_count": len(ctx.planned),
                    "unmatched_count": len(ctx.unmatched),
                    "applied_plans": bool(applied_plans),
                    "skip_reason": ctx.diagnostics.get("skip_reason") if isinstance(ctx.diagnostics, dict) else None,
                    "skipped_tracks": len(skipped_tracks) if isinstance(skipped_tracks, list) else 0,
                    "skipped_track_reasons": skipped_track_reasons,
                },
            )
        except Exception:  # pragma: no cover
            logger.exception("Failed to write directory audit event for %s", ctx.directory)
