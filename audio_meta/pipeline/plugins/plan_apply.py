from __future__ import annotations

import logging
import shutil

from ..contexts import PlanApplyContext
from ..protocols import PlanApplyPlugin
from ...models import ProcessingError

logger = logging.getLogger(__name__)


class DefaultPlanApplyPlugin(PlanApplyPlugin):
    name = "default_plan_apply"

    @staticmethod
    def _move_path(src, dest) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dest)
        except OSError:
            shutil.move(str(src), str(dest))

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
            moved_to = None
            if target_path:
                daemon.organizer.move(meta, target_path, dry_run=False)
                moved_to = meta.path

            if tag_changes:
                daemon.tag_writer.apply(meta)
                logger.debug("Updated tags for %s", meta.path)
            else:
                logger.debug("Tags already up to date for %s", meta.path)

            # Only record the move after tag application succeeds, so the cache reflects a
            # fully-applied plan (best-effort; we still attempt rollback on failures).
            if target_path and moved_to:
                daemon.cache.record_move(original_path, moved_to)
                daemon.organizer.cleanup_source_directory(original_path.parent)
        except ProcessingError as exc:
            if target_path and meta.path != original_path:
                try:
                    self._move_path(meta.path, original_path)
                    meta.path = original_path
                    logger.warning(
                        "Rolled back move for %s after failed tag update", original_path
                    )
                except Exception as rollback_exc:  # pragma: no cover
                    logger.warning(
                        "Failed to roll back move for %s after error: %s",
                        original_path,
                        rollback_exc,
                    )
            logger.warning("Failed to update tags for %s: %s", meta.path, exc)
            return False

        stat_after = daemon.services.safe_stat(meta.path)
        if stat_after:
            daemon.cache.set_processed_file(
                meta.path,
                stat_after.st_mtime_ns,
                stat_after.st_size,
                organized_flag,
            )
        return True
