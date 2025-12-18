from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..contexts import TrackSkipContext
from ..protocols import TrackSkipPolicyPlugin
from ..types import TrackSkipDecision

logger = logging.getLogger(__name__)


class DefaultTrackSkipPolicyPlugin(TrackSkipPolicyPlugin):
    name = "default_track_skip_policy"

    def should_skip(self, ctx: TrackSkipContext) -> Optional[TrackSkipDecision]:
        daemon = ctx.daemon
        if getattr(getattr(ctx, "directory_ctx", None), "force_prompt", False):
            return TrackSkipDecision(should_skip=False, reason="force_prompt")
        if daemon.dry_run_recorder:
            return TrackSkipDecision(should_skip=False)

        stat_before = daemon._safe_stat(ctx.file_path)
        if not stat_before:
            return TrackSkipDecision(should_skip=False)

        cached_state = daemon.cache.get_processed_file(ctx.file_path)
        if not cached_state:
            return TrackSkipDecision(should_skip=False)

        cached_mtime, cached_size, organized_flag = cached_state
        if (
            cached_mtime != stat_before.st_mtime_ns
            or cached_size != stat_before.st_size
        ):
            return TrackSkipDecision(should_skip=False)

        if daemon.organizer.enabled and not organized_flag:
            logger.debug(
                "Reprocessing %s because organizer is now enabled", ctx.file_path
            )
            return TrackSkipDecision(
                should_skip=False, reason="organizer_enabled_changed"
            )

        moved_target = daemon.cache.get_move(ctx.file_path)
        if moved_target and Path(moved_target).exists():
            logger.warning(
                "File %s already moved to %s; skipping stale copy",
                ctx.file_path,
                moved_target,
            )
            return TrackSkipDecision(should_skip=True, reason="stale_moved_copy")

        logger.debug("Skipping %s; already processed and unchanged", ctx.file_path)
        return TrackSkipDecision(should_skip=True, reason="already_processed_unchanged")
