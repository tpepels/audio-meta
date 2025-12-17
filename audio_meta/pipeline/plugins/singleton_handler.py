from __future__ import annotations

import logging
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import SingletonHandlerPlugin

logger = logging.getLogger(__name__)


class DefaultSingletonHandlerPlugin(SingletonHandlerPlugin):
    name = "default_singleton_handler"

    def resolve_release_home(self, ctx: DirectoryContext) -> Optional[object]:
        if not ctx.is_singleton:
            return None
        if not ctx.applied_provider or not ctx.applied_release_id:
            return None

        daemon = ctx.daemon
        release_key = daemon._release_key(ctx.applied_provider, ctx.applied_release_id)
        home_dir = daemon._select_singleton_release_home(
            release_key,
            ctx.directory,
            len(ctx.files),
            ctx.best_score,
            ctx.pending_results[0].meta if ctx.pending_results else None,
        )
        if home_dir:
            logger.debug(
                "Singleton directory %s relocating into %s",
                daemon._display_path(ctx.directory),
                daemon._display_path(home_dir),
            )
            ctx.release_home_dir = home_dir
            return home_dir
        ctx.release_home_dir = None
        return None
