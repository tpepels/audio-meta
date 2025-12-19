from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import directory_identity as directory_identity_logic


class DirectoryIdentityService:
    """Service for directory identity, path hints, and token-based matching."""

    def __init__(self) -> None:
        pass

    def path_based_hints(
        self, directory: Path
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Extract artist and album hints from directory path.

        Returns:
            Tuple of (artist_hint, album_hint)
        """
        return directory_identity_logic.path_based_hints(directory)

    def hint_cache_key(
        self, artist: Optional[str], album: Optional[str]
    ) -> Optional[str]:
        """
        Generate a cache key from artist/album hints.

        Returns normalized cache key for directory release lookups.
        """
        return directory_identity_logic.hint_cache_key(artist, album)

    def normalize_hint_value(self, value: Optional[str]) -> str:
        """
        Normalize a hint value for comparison.

        Converts to lowercase, removes extra whitespace, etc.
        """
        return directory_identity_logic.normalize_hint_value(value)

    def token_overlap_ratio(
        self, expected: Optional[str], candidate: Optional[str]
    ) -> float:
        """
        Calculate token overlap ratio between two strings.

        Returns a ratio from 0.0 to 1.0 indicating how many tokens match.
        Used for fuzzy matching of artist/album names.
        """
        return directory_identity_logic.token_overlap_ratio(expected, candidate)

    def tokenize(self, value: Optional[str]) -> list[str]:
        """
        Tokenize a string into normalized tokens.

        Returns a list of lowercase, normalized tokens for matching.
        """
        return directory_identity_logic.tokenize(value)

    def looks_like_disc_folder(self, name: str) -> bool:
        """
        Check if a directory name looks like a disc/CD folder.

        Examples: "CD1", "Disc 2", "Disk3"
        """
        return directory_identity_logic.looks_like_disc_folder(name)

    def directory_release_keys(
        self,
        directory: Path,
        artist_hint: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> list[str]:
        """
        Generate all possible cache keys for a directory.

        Combines path-based keys and hint-based keys.
        """
        keys: list[str] = []

        # Add path-based keys
        for path_key in self._release_path_keys(directory):
            if path_key not in keys:
                keys.append(path_key)

        # Add hint-based key
        path_artist, path_album = self.path_based_hints(directory)
        final_artist = artist_hint or path_artist
        final_album = album_hint or path_album
        canonical = self.hint_cache_key(final_artist, final_album)

        if canonical and canonical not in keys:
            keys.append(canonical)

        return keys

    def _release_path_keys(self, directory: Path) -> list[str]:
        """
        Generate path-based cache keys for a directory.

        Returns keys for both resolved path and album root.
        """
        from ..album_batching import AlbumBatcher

        paths: list[Path] = []

        try:
            resolved = directory.resolve()
        except FileNotFoundError:
            resolved = directory
        paths.append(resolved)

        album_root = AlbumBatcher.album_root(directory)
        if album_root != directory:
            try:
                root_resolved = album_root.resolve()
            except FileNotFoundError:
                root_resolved = album_root
            if root_resolved not in paths:
                paths.append(root_resolved)

        return [str(path) for path in paths]
