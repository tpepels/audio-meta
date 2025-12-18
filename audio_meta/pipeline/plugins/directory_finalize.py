from __future__ import annotations

import logging
from pathlib import Path

from ..contexts import DirectoryContext
from ..protocols import DirectoryFinalizePlugin

logger = logging.getLogger(__name__)


class DefaultDirectoryFinalizePlugin(DirectoryFinalizePlugin):
    name = "default_directory_finalize"

    def finalize(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        daemon = ctx.daemon
        if daemon.dry_run_recorder:
            return

        best_release_id = ctx.best_release_key
        if not best_release_id:
            return
        if not ctx.applied_provider or not ctx.applied_release_id:
            return

        directory_hash = ctx.directory_hash
        should_cache_release = bool(
            daemon.release_cache_enabled and directory_hash and best_release_id
        )

        def _cache_release_state() -> None:
            if not should_cache_release or not directory_hash:
                return
            provider, release_plain_id = daemon._split_release_key(best_release_id)
            effective_score = ctx.release_scores.get(best_release_id, ctx.best_score)
            if effective_score is None:
                effective_score = 1.0
            daemon.cache.set_release_by_hash(
                directory_hash, provider, release_plain_id, float(effective_score)
            )
            daemon.cache.set_directory_hash(ctx.directory, directory_hash)

        if not applied_plans:
            if not ctx.planned and not ctx.is_singleton:
                daemon._maybe_set_release_home(
                    daemon._release_key(ctx.applied_provider, ctx.applied_release_id),
                    daemon._album_root(ctx.directory),
                    track_count=ctx.dir_track_count
                    or daemon._count_audio_files(ctx.directory),
                    directory_hash=daemon.cache.get_directory_hash(ctx.directory),
                )
            _cache_release_state()
            return

        effective_score = ctx.release_scores.get(best_release_id, ctx.best_score)
        if effective_score is None:
            effective_score = 1.0

        destination_dirs: set[Path] = set()
        for plan in ctx.planned:
            target_path = getattr(plan, "target_path", None)
            if not target_path:
                continue
            if plan.meta.path != target_path:
                continue
            destination_dirs.add(daemon._album_root(plan.meta.path.parent))

        for dest_dir in destination_dirs:
            daemon._persist_directory_release(
                dest_dir,
                ctx.applied_provider,
                ctx.applied_release_id,
                float(effective_score),
            )
            if not ctx.is_singleton:
                daemon._maybe_set_release_home(
                    daemon._release_key(ctx.applied_provider, ctx.applied_release_id),
                    dest_dir,
                    track_count=daemon._count_audio_files(dest_dir),
                    directory_hash=daemon.cache.get_directory_hash(dest_dir),
                )

        _cache_release_state()

        if ctx.release_home_dir:
            moved_into_release_home = any(
                plan.target_path
                and daemon._path_under_directory(plan.target_path, ctx.release_home_dir)
                for plan in ctx.planned
            )
            if moved_into_release_home:
                daemon._reprocess_directory(ctx.release_home_dir)
