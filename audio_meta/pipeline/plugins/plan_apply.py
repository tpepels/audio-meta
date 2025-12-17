from __future__ import annotations

import logging

from ..contexts import PlanApplyContext
from ..protocols import PlanApplyPlugin
from ...models import ProcessingError

logger = logging.getLogger(__name__)


class DefaultPlanApplyPlugin(PlanApplyPlugin):
    name = "default_plan_apply"

    def apply(self, ctx: PlanApplyContext) -> bool:
        daemon = ctx.daemon
        plan = ctx.plan
        meta = plan.meta
        tag_changes = plan.tag_changes
        target_path = plan.target_path

        if daemon.dry_run_recorder:
            relocate_from = meta.path if target_path else None
            daemon.dry_run_recorder.record(
                meta,
                plan.score,
                tag_changes=tag_changes or None,
                relocate_from=relocate_from,
                relocate_to=target_path,
            )
            logger.debug("Dry-run recorded planned update for %s", meta.path)
            if target_path:
                daemon.organizer.move(meta, target_path, dry_run=True)
            return True

        original_path = meta.path
        organized_flag = daemon.organizer.enabled
        try:
            if target_path:
                daemon.organizer.move(meta, target_path, dry_run=False)
                daemon.cache.record_move(original_path, target_path)
                daemon.organizer.cleanup_source_directory(original_path.parent)
            if tag_changes:
                daemon.tag_writer.apply(meta)
                logger.debug("Updated tags for %s", meta.path)
            else:
                logger.debug("Tags already up to date for %s", meta.path)
        except ProcessingError as exc:
            logger.warning("Failed to update tags for %s: %s", meta.path, exc)
            return True

        stat_after = daemon._safe_stat(meta.path)
        if stat_after:
            daemon.cache.set_processed_file(
                meta.path,
                stat_after.st_mtime_ns,
                stat_after.st_size,
                organized_flag,
            )
        return True
