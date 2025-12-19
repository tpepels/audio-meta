"""
Identity canonicalization for artists and composers.

Performs a pre-scan library sweep to identify all artist and composer references
and normalize spelling variants into a single canonical identity.
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
    canonical: str
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
        result.artists = self._build_clusters(raw_artists)
        result.composers = self._build_clusters(raw_composers)
        result.album_artists = self._build_clusters(raw_album_artists)
        result.conductors = self._build_clusters(raw_conductors)
        result.performers = self._build_clusters(raw_performers)
        
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
        composer_values = audio.get("----:com.apple.iTunes:COMPOSER") or []
        for value in composer_values:
            if value:
                text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                for name in self._split_names(text):
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
        """Split a potentially multi-name value into individual names."""
        if not value:
            return []
        # Common delimiters for multiple artists/composers
        normalized = value.replace(" / ", ";").replace("/", ";").replace(" & ", ";")
        parts = [p.strip() for p in normalized.split(";") if p.strip()]
        return parts
    
    def _build_clusters(
        self, raw_names: dict[str, list[str]]
    ) -> dict[str, IdentityCluster]:
        """Build identity clusters from raw collected names."""
        clusters: dict[str, IdentityCluster] = {}
        
        for token, variants in raw_names.items():
            canonical = self._choose_canonical(variants)
            cluster = IdentityCluster(
                canonical=canonical,
                variants=set(variants),
                occurrences=len(variants),
            )
            clusters[token] = cluster
        
        return clusters
    
    def _choose_canonical(self, variants: list[str]) -> str:
        """
        Choose the best canonical form from a list of variants.
        
        Preference order:
        1. No comma (avoid "Last, First" format)
        2. Not all caps
        3. More words (fuller name)
        4. Longer (more complete)
        5. Alphabetically first (for consistency)
        """
        if not variants:
            return ""
        
        # Count occurrences
        counts: dict[str, int] = defaultdict(int)
        for v in variants:
            counts[v] += 1
        
        unique = list(counts.keys())
        
        def score(name: str) -> tuple:
            has_comma = 1 if "," in name else 0
            is_all_caps = 1 if name.isupper() else 0
            word_count = len([p for p in name.split() if p])
            length = len(name)
            frequency = counts[name]
            return (has_comma, is_all_caps, -word_count, -length, -frequency, name.casefold())
        
        unique.sort(key=score)
        return unique[0]
    
    @staticmethod
    def _normalize_token(value: str) -> str:
        """Normalize a name to a token for clustering."""
        if not value:
            return ""
        # Unicode normalization
        normalized = unicodedata.normalize("NFKD", value)
        # ASCII only
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        # Lowercase
        ascii_only = ascii_only.lower()
        # Remove non-alphanumeric
        ascii_only = re.sub(r"[^a-z0-9]+", " ", ascii_only)
        # Remove extra whitespace
        ascii_only = re.sub(r"\s+", "", ascii_only)
        return ascii_only


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
                # Create token for cache lookup
                cache_token = f"{category}::{token}"
                
                # Store the canonical form
                self.cache.set_canonical_name(cache_token, cluster.canonical)
                count += 1
                
                # Also store variants mapped to canonical
                for variant in cluster.variants:
                    variant_token = f"{category}::{IdentityScanner._normalize_token(variant)}"
                    if variant_token != cache_token:
                        self.cache.set_canonical_name(variant_token, cluster.canonical)
        
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
        
        token = IdentityScanner._normalize_token(name)
        if not token:
            return name
        
        cache_token = f"{category}::{token}"
        
        # Check local cache first
        if cache_token in self._local_cache:
            return self._local_cache[cache_token]
        
        # Check persistent cache
        canonical = self.cache.get_canonical_name(cache_token)
        if canonical:
            self._local_cache[cache_token] = canonical
            return canonical
        
        return name
    
    def canonicalize_multi(self, names: str, category: str = "artist") -> str:
        """
        Canonicalize a potentially multi-name string (e.g., "Artist1; Artist2").
        
        Returns the canonicalized names joined by "; ".
        """
        if not names:
            return names
        
        parts = [p.strip() for p in names.replace(" / ", ";").replace("/", ";").split(";")]
        canonical_parts = []
        
        for part in parts:
            if part:
                canonical = self.canonicalize(part, category)
                if canonical and canonical not in canonical_parts:
                    canonical_parts.append(canonical)
        
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
                print(f"  Occurrences: {cluster.occurrences}")
                if len(cluster.variants) > 1:
                    print(f"  Variants:")
                    for variant in sorted(cluster.variants):
                        if variant != cluster.canonical:
                            print(f"    - {variant}")
