"""
Improved identity canonicalization for artists and composers.

Fixes:
1. Properly handles comma-separated artist names
2. Better canonical name selection
3. Ensures uniqueness while maintaining user-friendly display names
4. More robust matching
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Iterator

from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

from .cache import MetadataCache
from .config import LibrarySettings

logger = logging.getLogger(__name__)


@dataclass
class IdentityCluster:
    """A cluster of name variants that should map to a single canonical identity."""
    canonical: str  # User-friendly display name
    canonical_id: str  # Unique identifier
    variants: set[str] = field(default_factory=set)
    occurrences: int = 0
    
    def add_variant(self, name: str) -> None:
        self.variants.add(name)
        self.occurrences += 1


@dataclass
class IdentityScanResult:
    """Result of a library identity scan."""
    artists: dict[str, IdentityCluster] = field(default_factory=dict)
    composers: dict[str, IdentityCluster] = field(default_factory=dict)
    album_artists: dict[str, IdentityCluster] = field(default_factory=dict)
    conductors: dict[str, IdentityCluster] = field(default_factory=dict)
    performers: dict[str, IdentityCluster] = field(default_factory=dict)
    total_files: int = 0
    
    @property
    def all_people(self) -> dict[str, dict[str, IdentityCluster]]:
        return {
            "artist": self.artists,
            "composer": self.composers,
            "album_artist": self.album_artists,
            "conductor": self.conductors,
            "performer": self.performers,
        }


class IdentityScanner:
    """
    Scans the library to collect all artist/composer references and build
    canonical identity mappings.
    """
    
    SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}
    
    # Common patterns to remove when normalizing
    FEAT_PATTERNS = [
        r'\s*\(?\s*feat\.?\s+[^)]+\)?',
        r'\s*\(?\s*featuring\s+[^)]+\)?',
        r'\s*\(?\s*ft\.?\s+[^)]+\)?',
        r'\s*\(?\s*with\s+[^)]+\)?',
    ]
    
    def __init__(
        self,
        settings: LibrarySettings,
        cache: Optional[MetadataCache] = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self._exts = {ext.lower() for ext in self.settings.include_extensions}
        
    def scan(self, progress_callback: Optional[Callable[[int, Path], None]] = None) -> IdentityScanResult:
        """
        Perform a full library sweep to collect all identity references.
        
        Args:
            progress_callback: Optional callback(files_scanned, current_path) for progress
            
        Returns:
            IdentityScanResult with clustered identities
        """
        result = IdentityScanResult()
        
        # Raw name collections before clustering
        raw_artists: dict[str, list[str]] = defaultdict(list)
        raw_composers: dict[str, list[str]] = defaultdict(list)
        raw_album_artists: dict[str, list[str]] = defaultdict(list)
        raw_conductors: dict[str, list[str]] = defaultdict(list)
        raw_performers: dict[str, list[str]] = defaultdict(list)
        
        for file_path in self._iter_audio_files():
            result.total_files += 1
            
            if progress_callback:
                progress_callback(result.total_files, file_path)
            
            names = self._extract_names(file_path)
            if not names:
                continue
                
            for name in names.get("artist", []):
                token = self._normalize_token(name)
                if token:
                    raw_artists[token].append(name)
                    
            for name in names.get("composer", []):
                token = self._normalize_token(name)
                if token:
                    raw_composers[token].append(name)
                    
            for name in names.get("album_artist", []):
                token = self._normalize_token(name)
                if token:
                    raw_album_artists[token].append(name)
                    
            for name in names.get("conductor", []):
                token = self._normalize_token(name)
                if token:
                    raw_conductors[token].append(name)
                    
            for name in names.get("performers", []):
                token = self._normalize_token(name)
                if token:
                    raw_performers[token].append(name)
        
        # Build clusters from raw data
        result.artists = self._build_clusters(raw_artists, "artist")
        result.composers = self._build_clusters(raw_composers, "composer")
        result.album_artists = self._build_clusters(raw_album_artists, "album_artist")
        result.conductors = self._build_clusters(raw_conductors, "conductor")
        result.performers = self._build_clusters(raw_performers, "performer")
        
        return result
    
    def _iter_audio_files(self) -> Iterator[Path]:
        """Iterate over all audio files in the library."""
        for root in self.settings.roots:
            if not root.exists():
                continue
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in self._exts:
                    continue
                yield file_path
    
    def _extract_names(self, path: Path) -> Optional[dict[str, list[str]]]:
        """Extract artist/composer names from a file's metadata."""
        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_EXTS:
            return None
            
        try:
            if ext == ".mp3":
                return self._extract_mp3(path)
            elif ext == ".flac":
                return self._extract_flac(path)
            elif ext == ".m4a":
                return self._extract_mp4(path)
        except Exception as exc:
            logger.debug("Failed to read %s: %s", path, exc)
            return None
        return None
    
    def _extract_mp3(self, path: Path) -> dict[str, list[str]]:
        """Extract names from MP3 file."""
        result: dict[str, list[str]] = defaultdict(list)
        try:
            tags = ID3(path)
        except Exception:
            return result
            
        # Artist (TPE1)
        for frame in tags.getall("TPE1"):
            if frame.text:
                for name in self._split_names(str(frame.text[0])):
                    if name:
                        result["artist"].append(name)
        
        # Album Artist (TPE2)
        for frame in tags.getall("TPE2"):
            if frame.text:
                for name in self._split_names(str(frame.text[0])):
                    if name:
                        result["album_artist"].append(name)
        
        # Composer (TCOM)
        for frame in tags.getall("TCOM"):
            if frame.text:
                for name in self._split_names(str(frame.text[0])):
                    if name:
                        result["composer"].append(name)
        
        # Conductor (TPE3)
        for frame in tags.getall("TPE3"):
            if frame.text:
                for name in self._split_names(str(frame.text[0])):
                    if name:
                        result["conductor"].append(name)
        
        return result
    
    def _extract_flac(self, path: Path) -> dict[str, list[str]]:
        """Extract names from FLAC file."""
        result: dict[str, list[str]] = defaultdict(list)
        try:
            audio = FLAC(path)
        except Exception:
            return result
        
        for tag_name, result_key in [
            ("ARTIST", "artist"),
            ("ALBUMARTIST", "album_artist"),
            ("ALBUM ARTIST", "album_artist"),
            ("COMPOSER", "composer"),
            ("CONDUCTOR", "conductor"),
            ("PERFORMER", "performers"),
            ("PERFORMERS", "performers"),
            ("SOLOIST", "performers"),
            ("ORCHESTRA", "performers"),
            ("ENSEMBLE", "performers"),
        ]:
            values = audio.get(tag_name, [])
            for value in values:
                if value:
                    for name in self._split_names(str(value)):
                        if name:
                            result[result_key].append(name)
        
        return result
    
    def _extract_mp4(self, path: Path) -> dict[str, list[str]]:
        """Extract names from M4A file."""
        result: dict[str, list[str]] = defaultdict(list)
        try:
            audio = MP4(path)
        except Exception:
            return result
        
        # Artist
        artist_values = audio.get("\xa9ART") or []
        for value in artist_values:
            if value:
                for name in self._split_names(str(value)):
                    if name:
                        result["artist"].append(name)
        
        # Album Artist
        album_artist_values = audio.get("aART") or []
        for value in album_artist_values:
            if value:
                for name in self._split_names(str(value)):
                    if name:
                        result["album_artist"].append(name)
        
        # Composer
        composer_values = audio.get("\xa9wrt") or []
        for value in composer_values:
            if value:
                for name in self._split_names(str(value)):
                    if name:
                        result["composer"].append(name)
        
        # Conductor
        conductor_values = audio.get("----:com.apple.iTunes:CONDUCTOR") or []
        for value in conductor_values:
            if value:
                text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                for name in self._split_names(text):
                    if name:
                        result["conductor"].append(name)
        
        return result
    
    def _split_names(self, value: str) -> list[str]:
        """
        Split a potentially multi-name value into individual names.
        
        FIXED: Now properly handles commas as delimiters to avoid "artist1,artist2" bugs.
        """
        if not value:
            return []
        
        # Step 1: Detect if this looks like "Last, First" format
        #   - Single comma with capitalized word after
        #   - No other delimiters
        is_lastname_first = False
        comma_count = value.count(",")
        if comma_count == 1 and ";" not in value and "/" not in value and "&" not in value:
            parts = value.split(",", 1)
            if len(parts) == 2 and parts[1].strip() and parts[1].strip()[0].isupper():
                # Likely "Last, First" - don't split on comma
                is_lastname_first = True
        
        # Step 2: Normalize delimiters
        normalized = value
        
        # Replace common multi-artist delimiters with semicolon
        if not is_lastname_first:
            # Treat comma as delimiter ONLY if it's not "Last, First" format
            normalized = normalized.replace(",", ";")
        
        # Other delimiters
        normalized = normalized.replace(" / ", ";")
        normalized = normalized.replace("/", ";")
        normalized = normalized.replace(" & ", ";")
        normalized = normalized.replace(" and ", ";")
        
        # Step 3: Split on semicolon
        parts = [p.strip() for p in normalized.split(";") if p.strip()]
        
        # Step 4: Clean each part
        cleaned = []
        for part in parts:
            # Remove featuring/feat patterns
            for pattern in self.FEAT_PATTERNS:
                part = re.sub(pattern, "", part, flags=re.IGNORECASE)
            part = part.strip()
            
            # Skip common non-person values
            if part.lower() in ("various", "various artists", "unknown", "n/a", ""):
                continue
            
            if part:
                cleaned.append(part)
        
        return cleaned
    
    def _build_clusters(
        self, raw_names: dict[str, list[str]], category: str
    ) -> dict[str, IdentityCluster]:
        """Build identity clusters from raw collected names."""
        clusters: dict[str, IdentityCluster] = {}
        
        for token, variants in raw_names.items():
            canonical = self._choose_canonical(variants)
            # Create unique ID by combining category and token
            canonical_id = f"{category}::{token}"
            
            cluster = IdentityCluster(
                canonical=canonical,
                canonical_id=canonical_id,
                variants=set(variants),
                occurrences=len(variants),
            )
            clusters[token] = cluster
        
        return clusters
    
    def _choose_canonical(self, variants: list[str]) -> str:
        """
        Choose the best canonical form from a list of variants.
        
        FIXED: Better logic that:
        1. Uses most common variant
        2. Prefers proper case over all caps
        3. Prefers fuller names
        4. Handles "Last, First" vs "First Last" intelligently
        """
        if not variants:
            return ""
        
        # Count occurrences
        counts: dict[str, int] = defaultdict(int)
        for v in variants:
            counts[v] += 1
        
        unique = list(counts.keys())
        
        def score(name: str) -> tuple:
            # Calculate scoring factors
            frequency = counts[name]
            is_all_caps = 1 if name.isupper() else 0
            is_all_lower = 1 if name.islower() else 0
            has_proper_case = 1 if not is_all_caps and not is_all_lower else 0
            word_count = len([p for p in name.split() if p])
            char_count = len(name)
            
            # Prefer "First Last" over "Last, First" format
            has_comma_space = 1 if ", " in name else 0
            
            # Return tuple for sorting (lower is better)
            # Order: proper case first, then frequency, then no comma, then word count, length
            return (
                -has_proper_case,  # Prefer proper case
                -frequency,  # Prefer more frequent
                has_comma_space,  # Avoid "Last, First"
                is_all_caps,  # Avoid all caps
                is_all_lower,  # Avoid all lowercase
                -word_count,  # Prefer more words
                -char_count,  # Prefer longer
                name.casefold(),  # Alphabetical tiebreaker
            )
        
        unique.sort(key=score)
        best = unique[0]
        
        # If best has "Last, First" format, try to convert to "First Last"
        if ", " in best:
            parts = best.split(", ", 1)
            if len(parts) == 2:
                first_last = f"{parts[1]} {parts[0]}"
                # Check if this form exists in variants
                if first_last in counts:
                    return first_last
        
        return best
    
    @staticmethod
    def _normalize_token(value: str) -> str:
        """
        Normalize a name to a token for clustering.
        
        This creates a unique identifier while preserving enough information
        to distinguish different people.
        """
        if not value:
            return ""
        
        # Remove featuring patterns first
        cleaned = value
        for pattern in IdentityScanner.FEAT_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Unicode normalization
        normalized = unicodedata.normalize("NFKD", cleaned)
        
        # Convert to ASCII
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        
        # Lowercase
        ascii_only = ascii_only.lower()
        
        # Remove punctuation but keep spaces temporarily
        ascii_only = re.sub(r"[^a-z0-9\s]+", " ", ascii_only)
        
        # Collapse multiple spaces
        ascii_only = re.sub(r"\s+", " ", ascii_only).strip()
        
        # Remove spaces for final token (but we kept them to preserve word boundaries)
        token = ascii_only.replace(" ", "")
        
        return token if token else ""


class IdentityCanonicalizer:
    """
    Applies canonical identity mappings during metadata processing.
    """
    
    def __init__(self, cache: MetadataCache) -> None:
        self.cache = cache
        self._local_cache: dict[str, str] = {}
    
    def apply_scan_results(self, result: IdentityScanResult) -> int:
        """
        Persist scan results to the cache for use during matching.
        
        Returns the number of canonical mappings created.
        """
        count = 0
        
        for category, clusters in result.all_people.items():
            for token, cluster in clusters.items():
                # Use the canonical_id as the key (ensures uniqueness)
                cache_key = cluster.canonical_id
                
                # Store the user-friendly canonical name
                self.cache.set_canonical_name(cache_key, cluster.canonical)
                count += 1
                
                # Store all variants that map to this canonical
                for variant in cluster.variants:
                    variant_token = IdentityScanner._normalize_token(variant)
                    if variant_token and variant_token != token:
                        variant_key = f"{category}::{variant_token}"
                        self.cache.set_canonical_name(variant_key, cluster.canonical)
                        count += 1
        
        logger.info("Persisted %d canonical identity mappings to cache", count)
        return count
    
    def canonicalize(self, name: str, category: str = "artist") -> str:
        """
        Get the canonical form of a name.
        
        Args:
            name: The name to canonicalize
            category: One of 'artist', 'composer', 'album_artist', 'conductor', 'performer'
            
        Returns:
            The canonical form, or the original name if no mapping exists
        """
        if not name:
            return name
        
        # Strip whitespace
        name = name.strip()
        
        token = IdentityScanner._normalize_token(name)
        if not token:
            return name
        
        cache_key = f"{category}::{token}"
        
        # Check local cache first
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]
        
        # Check persistent cache
        canonical = self.cache.get_canonical_name(cache_key)
        if canonical:
            self._local_cache[cache_key] = canonical
            return canonical
        
        return name
    
    def canonicalize_multi(self, names: str, category: str = "artist") -> str:
        """
        Canonicalize a potentially multi-name string.
        
        FIXED: Now properly returns individual names separated by "; " (NOT commas).
        """
        if not names:
            return names
        
        # Split using the same logic as scanner
        scanner = IdentityScanner(LibrarySettings(roots=[]), None)
        parts = scanner._split_names(names)
        
        canonical_parts = []
        seen = set()
        
        for part in parts:
            if part:
                canonical = self.canonicalize(part, category)
                # Avoid duplicates
                canonical_lower = canonical.lower()
                if canonical_lower not in seen:
                    canonical_parts.append(canonical)
                    seen.add(canonical_lower)
        
        # Always use semicolon as separator (NEVER comma)
        return "; ".join(canonical_parts) if canonical_parts else names


def run_prescan(
    settings: LibrarySettings,
    cache: MetadataCache,
    verbose: bool = False,
) -> IdentityScanResult:
    """
    Run the pre-scan identity canonicalization process.
    
    This should be called before the main matching scan to ensure
    consistent artist/composer identities.
    """
    logger.info("Starting identity pre-scan...")
    
    scanner = IdentityScanner(settings, cache)
    
    def progress(count: int, path: Path) -> None:
        if verbose and count % 100 == 0:
            logger.info("Scanned %d files...", count)
    
    result = scanner.scan(progress_callback=progress if verbose else None)
    
    logger.info(
        "Identity scan complete: %d files, %d artists, %d composers, %d album artists",
        result.total_files,
        len(result.artists),
        len(result.composers),
        len(result.album_artists),
    )
    
    # Persist to cache
    canonicalizer = IdentityCanonicalizer(cache)
    canonicalizer.apply_scan_results(result)
    
    return result


def print_identity_report(result: IdentityScanResult) -> None:
    """Print a human-readable report of identity clusters with variants."""
    print("\n=== Identity Canonicalization Report ===\n")
    
    for category, clusters in result.all_people.items():
        if not clusters:
            continue
        
        # Find clusters with multiple variants (potential issues)
        multi_variant = [
            (token, cluster) 
            for token, cluster in clusters.items() 
            if len(cluster.variants) > 1
        ]
        
        if multi_variant:
            print(f"\n--- {category.upper()} (entries with variants) ---")
            for token, cluster in sorted(multi_variant, key=lambda x: -x[1].occurrences)[:20]:
                print(f"\n  Canonical: {cluster.canonical}")
                print(f"  Unique ID: {cluster.canonical_id}")
                print(f"  Occurrences: {cluster.occurrences}")
                if len(cluster.variants) > 1:
                    print(f"  Variants:")
                    for variant in sorted(cluster.variants):
                        if variant != cluster.canonical:
                            print(f"    - {variant}")
