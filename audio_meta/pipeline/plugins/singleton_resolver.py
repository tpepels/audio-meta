"""
Pipeline plugin for unified singleton resolution.

Integrates the singleton resolution workflow into the pipeline,
determining if singletons are legitimate singles or misplaced tracks.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import SingletonHandlerPlugin
from ...singleton import (
    SingletonResolver,
    SingletonResolution,
    SingletonType,
    SingletonCause,
)

logger = logging.getLogger(__name__)


class UnifiedSingletonResolverPlugin(SingletonHandlerPlugin):
    """
    Unified singleton resolution plugin that combines:
    - Discogs single release lookup
    - MusicBrainz release type detection
    - Track-number detection from filename/metadata
    - Artist/composer consistency checks against on-disk releases
    - Automatic resolution when confidence is high
    """
    
    name = "unified_singleton_resolver"
    
    def resolve_release_home(self, ctx: DirectoryContext) -> Optional[object]:
        """Resolve singleton to appropriate release home."""
        if not ctx.is_singleton:
            return None
        
        daemon = ctx.daemon
        services = getattr(daemon, "services", None)
        cache = getattr(daemon, "cache", None)
        
        if not cache:
            return None
        
        # Get providers
        musicbrainz = getattr(daemon, "musicbrainz", None)
        discogs = getattr(daemon, "discogs", None) or getattr(services, "discogs", None)
        
        # Get library settings
        library_roots = []
        settings = getattr(daemon, "settings", None)
        if settings and hasattr(settings, "library"):
            library_roots = [r.resolve() for r in settings.library.roots]
        
        extensions = {".mp3", ".flac", ".m4a"}
        if settings and hasattr(settings, "library"):
            extensions = {ext.lower() for ext in settings.library.include_extensions}
        
        # Create resolver
        resolver = SingletonResolver(
            cache=cache,
            musicbrainz=musicbrainz,
            discogs=discogs,
            extensions=extensions,
            library_roots=library_roots,
        )
        
        # Get the singleton track metadata
        if not ctx.pending_results:
            return None
        
        pending = ctx.pending_results[0]
        meta = pending.meta
        if not meta:
            return None
        
        # Read existing tags for additional signals
        existing_tags = None
        tag_writer = getattr(daemon, "tag_writer", None)
        if tag_writer:
            try:
                existing_tags = tag_writer.read_existing_tags(meta)
            except Exception:
                pass
        
        # Run the unified resolution workflow
        resolution = resolver.resolve(
            directory=ctx.directory,
            meta=meta,
            existing_tags=existing_tags,
        )
        
        # Store diagnostics
        ctx.diagnostics["singleton_resolution"] = {
            "type": resolution.singleton_type.value,
            "cause": resolution.cause.value,
            "auto_resolvable": resolution.auto_resolvable,
            "candidate_count": len(resolution.candidates),
            "explanation": resolution.explanation,
        }
        
        # Log the classification
        logger.debug(
            "Singleton %s classified as %s (cause: %s, auto-resolve: %s)",
            ctx.directory,
            resolution.singleton_type.value,
            resolution.cause.value,
            resolution.auto_resolvable,
        )
        
        # Handle legitimate singles - no relocation needed
        if resolution.singleton_type == SingletonType.LEGITIMATE_SINGLE:
            logger.info(
                "Singleton %s is a legitimate single release, no relocation needed",
                services.display_path(ctx.directory) if services else ctx.directory,
            )
            ctx.release_home_dir = None
            return None
        
        # Handle auto-resolvable cases
        if resolution.auto_resolvable and resolution.best_candidate:
            home_dir = resolution.best_candidate.directory
            logger.info(
                "Auto-resolving singleton %s -> %s (confidence: %.0f%%)",
                services.display_path(ctx.directory) if services else ctx.directory,
                services.display_path(home_dir) if services else home_dir,
                resolution.best_candidate.confidence * 100,
            )
            ctx.release_home_dir = home_dir
            ctx.diagnostics["singleton_auto_resolved"] = True
            return home_dir
        
        # Handle cases that need prompting
        if resolution.should_prompt and resolution.best_candidate:
            # Check if we should defer prompts
            defer_prompts = getattr(daemon, "defer_prompts", False)
            processing_deferred = getattr(daemon, "_processing_deferred", False)
            
            if defer_prompts and not processing_deferred:
                # Schedule for later prompting
                if services:
                    services.schedule_deferred_directory(
                        ctx.directory, 
                        f"singleton_resolution:{resolution.cause.value}"
                    )
                logger.debug(
                    "Deferring singleton resolution for %s",
                    ctx.directory,
                )
                return None
            
            # Present options to user
            home_dir = self._prompt_user_choice(ctx, resolution, daemon)
            if home_dir:
                ctx.release_home_dir = home_dir
                return home_dir
        
        # No resolution found
        ctx.release_home_dir = None
        return None
    
    def _prompt_user_choice(
        self,
        ctx: DirectoryContext,
        resolution: SingletonResolution,
        daemon: object,
    ) -> Optional[Path]:
        """Present singleton resolution options to the user."""
        services = getattr(daemon, "services", None)
        prompt_io = getattr(daemon, "prompt_io", None)
        
        if not prompt_io:
            return None
        
        display_dir = services.display_path(ctx.directory) if services else str(ctx.directory)
        
        # Build prompt
        prompt_io.print(f"\n{'='*60}")
        prompt_io.print(f"SINGLETON RESOLUTION: {display_dir}")
        prompt_io.print(f"{'='*60}")
        prompt_io.print(f"\n{resolution.explanation}\n")
        
        if not resolution.candidates:
            prompt_io.print("No destination candidates found.")
            prompt_io.print("\nOptions:")
            prompt_io.print("  [s] Skip this singleton")
            prompt_io.print("  [i] Ignore this directory permanently")
            
            try:
                choice = prompt_io.input("\nChoice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None
            
            if choice == "i":
                cache = getattr(daemon, "cache", None)
                if cache:
                    cache.ignore_directory(ctx.directory, "user_ignored_singleton")
            return None
        
        # Show candidates
        prompt_io.print("Candidate destinations:")
        for i, candidate in enumerate(resolution.candidates[:5], 1):
            display_cand = services.display_path(candidate.directory) if services else str(candidate.directory)
            prompt_io.print(f"  [{i}] {display_cand}")
            prompt_io.print(f"      Confidence: {candidate.confidence:.0%}")
            prompt_io.print(f"      Tracks: {candidate.track_count}")
            if candidate.match_reasons:
                prompt_io.print(f"      Reasons: {', '.join(candidate.match_reasons)}")
        
        prompt_io.print("\nOptions:")
        prompt_io.print("  [1-5] Select destination")
        prompt_io.print("  [s] Skip this singleton")
        prompt_io.print("  [i] Ignore this directory permanently")
        
        try:
            choice = prompt_io.input("\nChoice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        
        if choice == "i":
            cache = getattr(daemon, "cache", None)
            if cache:
                cache.ignore_directory(ctx.directory, "user_ignored_singleton")
            return None
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(resolution.candidates):
                return resolution.candidates[idx].directory
        
        return None
