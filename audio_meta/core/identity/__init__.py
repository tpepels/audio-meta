"""
Identity and canonicalization domain logic.

This module handles:
- Name normalization and tokenization
- Identity clustering (grouping variants)
- Canonical name selection
- Name matching strategies (exact, substring, initials, fuzzy)
- Canonical name mapping and persistence

All logic is pure business logic with minimal I/O dependencies.
"""

from __future__ import annotations

from .canonicalizer import IdentityCanonicalizer
from .matching import NameMatcher, normalize_token
from .models import IdentityCluster, IdentityScanResult, MatchResult
from .scanner import IdentityScanner

__all__ = [
    "IdentityCanonicalizer",
    "IdentityCluster",
    "IdentityScanner",
    "IdentityScanResult",
    "MatchResult",
    "NameMatcher",
    "normalize_token",
]
