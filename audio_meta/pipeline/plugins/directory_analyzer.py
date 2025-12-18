from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import DirectoryAnalyzerPlugin


class DefaultDirectoryAnalyzerPlugin(DirectoryAnalyzerPlugin):
    name = "default_directory_analyzer"

    def analyze(self, ctx: DirectoryContext) -> None:
        daemon = ctx.daemon
        try:
            count, year = daemon._directory_context(ctx.directory, ctx.files)
        except Exception:
            return
        ctx.dir_track_count = count
        ctx.dir_year = year
