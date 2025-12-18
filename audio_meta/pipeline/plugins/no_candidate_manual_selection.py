from __future__ import annotations

import logging
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import ReleaseDecisionPlugin
from ...daemon_types import ReleaseExample
from ...release_selection import ReleaseDecision

logger = logging.getLogger(__name__)


class NoCandidateManualSelectionPlugin(ReleaseDecisionPlugin):
    name = "no_candidate_manual_selection"

    def decide(self, ctx: DirectoryContext) -> Optional[ReleaseDecision]:
        daemon = ctx.daemon
        services = daemon.services
        if ctx.release_scores:
            return None
        if not daemon.interactive:
            return None
        sample_meta = ctx.pending_results[0].meta if ctx.pending_results else None

        if (
            daemon.defer_prompts
            and not ctx.force_prompt
            and not services.processing_deferred
        ):
            services.schedule_deferred_directory(ctx.directory, "no_release_candidates")
            return ReleaseDecision(
                best_release_id=None,
                best_score=0.0,
                ambiguous_candidates=[],
                coverage=1.0,
                forced_provider=None,
                forced_release_id=None,
                forced_release_score=0.0,
                discogs_release_details=None,
                release_summary_printed=ctx.release_summary_printed,
                should_abort=True,
            )

        if sample_meta and ctx.dir_track_count and sample_meta.track_total is None:
            sample_meta.track_total = int(ctx.dir_track_count)
        selection = services.resolve_unmatched_directory(
            ctx.directory,
            sample_meta,
            ctx.dir_track_count,
            ctx.dir_year,
            files=list(ctx.files),
        )
        if selection is None:
            logger.warning("Skipping %s; no manual release selected", ctx.directory)
            return ReleaseDecision(
                best_release_id=None,
                best_score=0.0,
                ambiguous_candidates=[],
                coverage=1.0,
                forced_provider=None,
                forced_release_id=None,
                forced_release_score=0.0,
                discogs_release_details=None,
                release_summary_printed=ctx.release_summary_printed,
                should_abort=True,
            )

        provider, selection_id = selection
        if provider == "discogs":
            if not daemon.discogs:
                services.record_skip(
                    ctx.directory, "Discogs provider unavailable for manual selection"
                )
                logger.warning(
                    "Discogs provider unavailable; cannot apply manual selection for %s",
                    ctx.directory,
                )
                return ReleaseDecision(
                    best_release_id=None,
                    best_score=0.0,
                    ambiguous_candidates=[],
                    coverage=1.0,
                    forced_provider="discogs",
                    forced_release_id=selection_id,
                    forced_release_score=1.0,
                    discogs_release_details=None,
                    release_summary_printed=ctx.release_summary_printed,
                    should_abort=True,
                )
            details = services.fetch_discogs_release(selection_id)
            if not details:
                services.record_skip(
                    ctx.directory, f"Failed to load Discogs release {selection_id}"
                )
                logger.warning(
                    "Failed to load Discogs release %s; skipping %s",
                    selection_id,
                    ctx.directory,
                )
                return ReleaseDecision(
                    best_release_id=None,
                    best_score=0.0,
                    ambiguous_candidates=[],
                    coverage=1.0,
                    forced_provider="discogs",
                    forced_release_id=selection_id,
                    forced_release_score=1.0,
                    discogs_release_details=None,
                    release_summary_printed=ctx.release_summary_printed,
                    should_abort=True,
                )
            key = services.release_key("discogs", selection_id)
            ctx.discogs_details[key] = details
            ctx.release_examples[key] = ReleaseExample(
                provider="discogs",
                title=details.get("title") or "",
                artist=services.discogs_release_artist(details) or "",
                date=str(details.get("year") or ""),
                track_total=len(details.get("tracklist") or []),
                disc_count=details.get("disc_count"),
                formats=details.get("formats") or [],
            )
            ctx.release_scores[key] = max(ctx.release_scores.get(key, 0.0), 1.0)
            return ReleaseDecision(
                best_release_id=key,
                best_score=1.0,
                ambiguous_candidates=[(key, 1.0)],
                coverage=1.0,
                forced_provider="discogs",
                forced_release_id=selection_id,
                forced_release_score=1.0,
                discogs_release_details=details,
                release_summary_printed=ctx.release_summary_printed,
                should_abort=False,
            )

        applied = services.apply_musicbrainz_release_selection(
            ctx.directory,
            selection_id,
            ctx.pending_results,
            force=True,
        )
        if not applied:
            services.record_skip(
                ctx.directory,
                f"Manual MusicBrainz release {selection_id} did not match tracks",
            )
            logger.warning(
                "Manual MusicBrainz release %s did not match tracks in %s",
                selection_id,
                ctx.directory,
            )
            return ReleaseDecision(
                best_release_id=None,
                best_score=0.0,
                ambiguous_candidates=[],
                coverage=1.0,
                forced_provider="musicbrainz",
                forced_release_id=selection_id,
                forced_release_score=1.0,
                discogs_release_details=None,
                release_summary_printed=ctx.release_summary_printed,
                should_abort=True,
            )
        key = services.release_key("musicbrainz", selection_id)
        ctx.release_scores[key] = max(ctx.release_scores.get(key, 0.0), 1.0)
        return ReleaseDecision(
            best_release_id=key,
            best_score=1.0,
            ambiguous_candidates=[(key, 1.0)],
            coverage=1.0,
            forced_provider="musicbrainz",
            forced_release_id=selection_id,
            forced_release_score=1.0,
            discogs_release_details=None,
            release_summary_printed=ctx.release_summary_printed,
            should_abort=False,
        )
