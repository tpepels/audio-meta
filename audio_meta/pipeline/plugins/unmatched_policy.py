from __future__ import annotations

import logging
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import UnmatchedPolicyPlugin
from ..types import UnmatchedDecision

logger = logging.getLogger(__name__)


class DefaultUnmatchedPolicyPlugin(UnmatchedPolicyPlugin):
    name = "default_unmatched_policy"

    def decide(self, ctx: DirectoryContext) -> Optional[UnmatchedDecision]:
        daemon = ctx.daemon
        services = daemon.services
        if not ctx.best_release_key:
            return UnmatchedDecision(should_abort=False)
        if (
            daemon.defer_prompts
            and not ctx.force_prompt
            and not daemon._processing_deferred
        ):
            services.schedule_deferred_directory(ctx.directory, "unmatched_tracks")
            return UnmatchedDecision(should_abort=True)
        if daemon.interactive:
            services.log_unmatched_candidates(
                ctx.directory, ctx.best_release_key, ctx.unmatched
            )
            if not services.prompt_on_unmatched_release(
                ctx.directory, ctx.best_release_key, ctx.unmatched
            ):
                services.record_skip(
                    ctx.directory, "User skipped due to unmatched tracks"
                )
                logger.warning(
                    "Skipping %s because %d tracks did not match release %s",
                    ctx.directory,
                    len(ctx.unmatched),
                    ctx.best_release_key,
                )
                return UnmatchedDecision(should_abort=True)
            return UnmatchedDecision(should_abort=False)
        logger.warning(
            "Release %s left %d tracks unmatched in %s",
            ctx.best_release_key,
            len(ctx.unmatched),
            services.display_path(ctx.directory),
        )
        return UnmatchedDecision(should_abort=False)
