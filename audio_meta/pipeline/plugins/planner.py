from __future__ import annotations

import logging

from ..contexts import DirectoryContext
from ..protocols import PlannerPlugin
from ...daemon_types import PlannedUpdate

logger = logging.getLogger(__name__)


class DefaultPlannerPlugin(PlannerPlugin):
    name = "default_planner"

    def build(self, ctx: DirectoryContext) -> list[PlannedUpdate] | None:
        daemon = ctx.daemon
        planned: list[PlannedUpdate] = []

        classical_flags: dict[object, bool] = {}
        composers: list[str] = []
        performers: list[str] = []

        for pending in ctx.pending_results:
            meta = pending.meta
            if not pending.matched:
                continue

            if ctx.best_release_key and ctx.applied_provider and ctx.applied_release_id:
                if ctx.album_name:
                    meta.album = ctx.album_name
                if ctx.album_artist:
                    meta.album_artist = ctx.album_artist
                if ctx.applied_provider == "musicbrainz":
                    meta.musicbrainz_release_id = ctx.applied_release_id
                else:
                    meta.musicbrainz_release_id = None

            is_classical = daemon.heuristics.adapt_metadata(meta)
            classical_flags[meta.path] = is_classical

            if meta.composer:
                composers.append(meta.composer)
            if meta.album_artist:
                performers.append(meta.album_artist)
            if meta.artist:
                performers.append(meta.artist)
            if meta.conductor:
                performers.append(meta.conductor)
            if meta.performers:
                performers.extend(meta.performers)

        daemon.organizer.prime_canonical_people(
            composers=composers,
            performers=performers,
        )

        for pending in ctx.pending_results:
            meta = pending.meta
            result = pending.result
            if not pending.matched:
                logger.warning(
                    "No metadata match for %s; leaving file untouched", meta.path
                )
                continue

            is_classical = bool(classical_flags.get(meta.path, False))
            daemon.organizer.canonicalize_people_fields(meta)
            tag_changes = daemon.tag_writer.diff(meta)
            target_path = daemon.organizer.plan_target(meta, is_classical)

            if not tag_changes and not target_path:
                logger.debug("No changes required for %s", meta.path)
                continue

            planned.append(
                PlannedUpdate(
                    meta=meta,
                    score=result.score if result else None,
                    tag_changes=tag_changes,
                    target_path=target_path,
                )
            )

        ctx.planned = planned
        return planned
