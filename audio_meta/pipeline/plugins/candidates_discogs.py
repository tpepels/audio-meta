from __future__ import annotations

from ..contexts import DirectoryContext
from ..protocols import CandidateSourcePlugin
from ...daemon_types import ReleaseExample


class DiscogsCandidateSourcePlugin(CandidateSourcePlugin):
    name = "discogs_candidate_source"

    def add(self, ctx: DirectoryContext) -> None:
        daemon = ctx.daemon
        services = daemon.services
        if not getattr(daemon, "discogs", None):
            return
        if not ctx.pending_results:
            return
        sample_meta = ctx.pending_results[0].meta
        if ctx.dir_track_count:
            sample_meta.extra.setdefault("TRACK_TOTAL", str(ctx.dir_track_count))
        for cand in services.discogs_candidates(sample_meta):
            release_id = str(cand.get("id"))
            if not release_id:
                continue
            key = services.release_key("discogs", release_id)
            if key in ctx.release_scores:
                continue
            base_score = cand.get("score")
            base_score = base_score if isinstance(base_score, (int, float)) else 0.5
            ctx.release_scores[key] = float(base_score)
            ctx.release_examples[key] = ReleaseExample(
                provider="discogs",
                title=cand.get("title") or "",
                artist=cand.get("artist") or "",
                date=str(cand.get("year") or ""),
                track_total=cand.get("track_count"),
                disc_count=cand.get("disc_count"),
                formats=list(cand.get("formats") or []),
            )
            details = cand.get("details")
            if details:
                ctx.discogs_details[key] = details
