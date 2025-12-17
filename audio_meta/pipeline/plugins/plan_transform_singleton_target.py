from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import PlanTransformPlugin


class SingletonTargetOverridePlugin(PlanTransformPlugin):
    name = "singleton_target_override"

    def transform(self, ctx: DirectoryContext) -> None:
        if not ctx.release_home_dir:
            return
        daemon = ctx.daemon
        for plan in ctx.planned:
            if not getattr(plan, "target_path", None):
                continue
            meta = plan.meta
            is_classical = daemon.heuristics.adapt_metadata(meta)
            override_target = daemon._plan_singleton_target(meta, ctx.release_home_dir, is_classical)
            if override_target and not daemon._path_under_directory(meta.path, ctx.release_home_dir):
                plan.target_path = override_target

