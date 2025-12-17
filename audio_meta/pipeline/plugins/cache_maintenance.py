from __future__ import annotations

import logging

from ..protocols import CacheMaintenancePlugin

logger = logging.getLogger(__name__)


class DefaultCacheMaintenancePlugin(CacheMaintenancePlugin):
    name = "default_cache_maintenance"

    def after_scan(self, daemon: object) -> None:
        cache = getattr(daemon, "cache", None)
        if cache is None:
            return
        try:
            removed_moves = cache.prune_missing_moves()
            removed_processed = cache.prune_missing_processed_files(max_entries=10_000)
            removed_homes = cache.prune_missing_release_homes()
            removed_deferred = cache.prune_missing_deferred_prompts()
        except Exception:  # pragma: no cover
            logger.exception("Cache maintenance failed")
            return
        if removed_moves or removed_processed or removed_homes or removed_deferred:
            logger.info(
                "Cache maintenance: removed %d stale move(s), %d missing processed file(s), %d missing release home(s), %d missing deferred prompt(s)",
                removed_moves,
                removed_processed,
                removed_homes,
                removed_deferred,
            )
