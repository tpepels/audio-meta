from __future__ import annotations

from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import ReleaseDecisionPlugin
from ...release_selection import ReleaseDecision, decide_release


class DefaultReleaseDecisionPlugin(ReleaseDecisionPlugin):
    name = "default_release_selection"

    def decide(self, ctx: DirectoryContext) -> Optional[ReleaseDecision]:
        return decide_release(
            ctx.daemon,
            ctx.directory,
            len(ctx.files),
            ctx.is_singleton,
            ctx.dir_track_count,
            ctx.dir_year,
            ctx.pending_results,
            ctx.release_scores,
            ctx.release_examples,
            ctx.discogs_details,
            ctx.forced_provider,
            ctx.forced_release_id,
            ctx.forced_release_score,
            ctx.force_prompt,
            ctx.release_summary_printed,
        )

