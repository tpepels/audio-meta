from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import DirectoryDiagnosticsPlugin


class DefaultDirectoryDiagnosticsPlugin(DirectoryDiagnosticsPlugin):
    name = "default_directory_diagnostics"

    def run(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        if applied_plans:
            return
        if ctx.planned:
            return
        if any(p.matched for p in ctx.pending_results):
            return
        ctx.daemon._record_skip(ctx.directory, "No metadata match found for directory")

