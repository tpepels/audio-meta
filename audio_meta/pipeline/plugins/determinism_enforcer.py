"""
Pipeline plugin to enforce deterministic behavior across scans.

This plugin ensures that directories don't get reprocessed unnecessarily
by properly tracking what has been processed and caching decisions correctly.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from ..contexts import DirectoryContext
from ..protocols import DirectoryFinalizePlugin
from ...determinism import DeterminismChecker

logger = logging.getLogger(__name__)


class DeterminismEnforcerPlugin(DirectoryFinalizePlugin):
    """
    Ensures deterministic behavior by:
    1. Computing stable content hashes (excluding metadata-only changes)
    2. Properly persisting release decisions
    3. Recording match attempts for debugging
    """
    
    name = "determinism_enforcer"
    
    def finalize(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        """Record processing and ensure proper persistence."""
        daemon = ctx.daemon
        cache = getattr(daemon, "cache", None)
        if not cache:
            return
        
        checker = DeterminismChecker(cache)
        
        # Compute a stable content hash that doesn't include mtime
        # This prevents re-prompting when only tags have changed
        stable_hash = self._compute_stable_hash(ctx)
        
        # Get the applied release info
        provider = ctx.applied_provider
        release_id = ctx.applied_release_id
        
        if provider and release_id and stable_hash:
            # Ensure the decision is properly persisted
            checker.ensure_decision_persisted(
                directory=ctx.directory,
                provider=provider,
                release_id=release_id,
                score=ctx.best_score if hasattr(ctx, "best_score") else 0.8,
                directory_hash=stable_hash,
            )
            
            # Record the match attempt for debugging
            checker.record_match_attempt(
                directory=ctx.directory,
                provider=provider,
                release_id=release_id,
                score=ctx.best_score if hasattr(ctx, "best_score") else 0.0,
                directory_hash=stable_hash,
                was_prompted=ctx.force_prompt or ctx.require_release_confirmation,
                outcome="applied" if applied_plans else "skipped",
            )
            
            logger.debug(
                "Persisted deterministic state for %s: %s:%s (stable_hash=%s)",
                ctx.directory,
                provider,
                release_id,
                stable_hash[:8],
            )
    
    def _compute_stable_hash(self, ctx: DirectoryContext) -> Optional[str]:
        """
        Compute a content hash that remains stable when only metadata changes.
        
        Uses:
        - Sorted filenames
        - File sizes
        - NOT modification times (to allow tag writes)
        """
        if not ctx.files:
            return None
        
        hasher = hashlib.sha256()
        
        # Include organizer settings that affect output
        daemon = ctx.daemon
        organizer = getattr(daemon, "organizer", None)
        if organizer:
            settings = getattr(organizer, "settings", None)
            if settings:
                hasher.update(f"org_enabled:{getattr(organizer, 'enabled', True)}".encode())
                hasher.update(f"classical_strat:{getattr(settings, 'classical_mixed_strategy', '')}".encode())
                hasher.update(f"max_len:{getattr(settings, 'max_filename_length', 0)}".encode())
        
        # Sort files for determinism
        sorted_files = sorted(ctx.files, key=lambda p: str(p))
        
        for file_path in sorted_files:
            try:
                stat = file_path.stat()
                # Use relative path name
                rel_name = file_path.name
                hasher.update(rel_name.encode("utf-8"))
                hasher.update(str(stat.st_size).encode("utf-8"))
                # Do NOT include mtime - this is the key fix
            except (OSError, FileNotFoundError):
                continue
        
        return hasher.hexdigest()
