from __future__ import annotations

from ..contexts import TrackSignalContext
from ..protocols import SignalExtractorPlugin


class DefaultSignalExtractorPlugin(SignalExtractorPlugin):
    name = "default_signal_extractor"

    def extract(self, ctx: TrackSignalContext) -> None:
        if ctx.existing_tags:
            ctx.daemon._apply_tag_hints(ctx.meta, ctx.existing_tags)
