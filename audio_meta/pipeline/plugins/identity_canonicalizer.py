"""
Pipeline plugin for applying canonical identity mappings.

Ensures artist/composer names are normalized early in the pipeline
using mappings built by the pre-scan identity process.
"""
from __future__ import annotations

import logging

from ..contexts import DirectoryContext
from ..protocols import DirectoryInitializerPlugin
from ...identity import IdentityCanonicalizer

logger = logging.getLogger(__name__)


class IdentityCanonicalizePlugin(DirectoryInitializerPlugin):
    """
    Applies canonical identity mappings to track metadata during
    directory initialization.
    
    This plugin should run early in the pipeline to ensure all
    downstream matching uses normalized artist/composer identities.
    """
    
    name = "identity_canonicalizer"
    
    def initialize(self, ctx: DirectoryContext) -> None:
        """Apply canonical names to all pending track metadata."""
        daemon = ctx.daemon
        cache = getattr(daemon, "cache", None)
        if not cache:
            return
        
        canonicalizer = IdentityCanonicalizer(cache)
        
        for pending in ctx.pending_results:
            meta = pending.meta
            if not meta:
                continue
            
            # Canonicalize each people field
            if meta.artist:
                meta.artist = canonicalizer.canonicalize_multi(
                    meta.artist, "artist"
                )
            
            if meta.album_artist:
                meta.album_artist = canonicalizer.canonicalize_multi(
                    meta.album_artist, "album_artist"
                )
            
            if meta.composer:
                meta.composer = canonicalizer.canonicalize_multi(
                    meta.composer, "composer"
                )
            
            if meta.conductor:
                meta.conductor = canonicalizer.canonicalize_multi(
                    meta.conductor, "conductor"
                )
            
            if meta.performers:
                canonical_performers = []
                for performer in meta.performers:
                    canonical = canonicalizer.canonicalize(performer, "performer")
                    if canonical and canonical not in canonical_performers:
                        canonical_performers.append(canonical)
                meta.performers = canonical_performers or meta.performers
        
        # Also canonicalize album_artist context field if present
        if ctx.album_artist:
            ctx.album_artist = canonicalizer.canonicalize_multi(
                ctx.album_artist, "album_artist"
            )
