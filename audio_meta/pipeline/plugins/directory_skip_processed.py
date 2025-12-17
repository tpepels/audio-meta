from __future__ import annotations

import logging
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import DirectorySkipPolicyPlugin

logger = logging.getLogger(__name__)


class ProcessedDirectorySkipPolicyPlugin(DirectorySkipPolicyPlugin):
    name = "processed_directory_skip"

    def should_skip(self, ctx: DirectoryContext) -> Optional[bool]:
        daemon = ctx.daemon
        if ctx.force_prompt:
            return False
        if not ctx.files:
            return False
        if daemon.dry_run_recorder:
            return False
        for file_path in ctx.files:
            stat = daemon._safe_stat(file_path)
            if not stat:
                return False
            cached = daemon.cache.get_processed_file(file_path)
            if not cached:
                return False
            cached_mtime, cached_size, organized_flag = cached
            if cached_mtime != stat.st_mtime_ns or cached_size != stat.st_size or not organized_flag:
                return False
        logger.debug("Skipping %s; directory already processed and organized", daemon._display_path(ctx.directory))
        ctx.diagnostics.setdefault("skip_reason", "directory_already_processed")
        return True
