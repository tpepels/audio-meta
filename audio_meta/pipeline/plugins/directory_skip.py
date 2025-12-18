from __future__ import annotations

import logging
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import DirectorySkipPolicyPlugin

logger = logging.getLogger(__name__)


class DefaultDirectorySkipPolicyPlugin(DirectorySkipPolicyPlugin):
    name = "default_directory_skip"

    def should_skip(self, ctx: DirectoryContext) -> Optional[bool]:
        daemon = ctx.daemon
        if ctx.force_prompt:
            return False
        if not getattr(daemon, "release_cache_enabled", True):
            return False
        if not ctx.directory_hash:
            return False
        if ctx.hash_release_entry is None:
            ctx.hash_release_entry = daemon.cache.get_release_by_hash(
                ctx.directory_hash
            )
        if ctx.cached_directory_hash is None:
            ctx.cached_directory_hash = daemon.cache.get_directory_hash(ctx.directory)
        if (
            ctx.cached_directory_hash
            and ctx.cached_directory_hash == ctx.directory_hash
            and ctx.hash_release_entry
        ):
            logger.debug(
                "Skipping %s; directory unchanged (hash=%s)",
                daemon._display_path(ctx.directory),
                ctx.directory_hash[:8],
            )
            ctx.diagnostics.setdefault("skip_reason", "directory_hash_unchanged")
            return True
        return False
