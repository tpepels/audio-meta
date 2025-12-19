"""
Data validation layer for incoming metadata.

Ensures that metadata from providers (MusicBrainz, Discogs) and user input
is sanitized and validated before being applied to the library.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation operation."""
    valid: bool
    sanitized_value: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MetadataValidator:
    """
    Validates and sanitizes metadata from external sources.
    
    Ensures data quality and prevents corruption from invalid metadata.
    """
    
    # Control characters that should never appear in metadata
    CONTROL_CHARS = set(range(0x00, 0x20)) - {0x09, 0x0A, 0x0D}  # Exclude tab, LF, CR
    
    # Maximum reasonable lengths
    MAX_ARTIST_LENGTH = 500
    MAX_ALBUM_LENGTH = 500
    MAX_TITLE_LENGTH = 500
    MAX_GENRE_LENGTH = 100
    MAX_YEAR = 2100
    MIN_YEAR = 1800
    MAX_TRACK_NUMBER = 999
    MAX_DISC_NUMBER = 99
    MAX_DURATION_SECONDS = 7200  # 2 hours
    
    @classmethod
    def validate_artist(cls, value: Optional[str]) -> ValidationResult:
        """
        Validate and sanitize artist name.
        
        IMPORTANT: Artists must be atomic - only ONE artist per field/directory.
        Multiple artists are NOT allowed (no "artist1; artist2" or "artist1, artist2").
        
        Rules:
        - Remove control characters
        - Normalize unicode
        - Check length
        - Remove leading/trailing whitespace
        - ENFORCE: Single artist only (no delimiters like semicolon, slash, ampersand)
        """
        if not value:
            return ValidationResult(valid=True, sanitized_value="")
        
        result = ValidationResult(valid=True)
        sanitized = value
        
        # Normalize unicode
        sanitized = unicodedata.normalize("NFC", sanitized)
        
        # Remove control characters
        sanitized = cls._remove_control_characters(sanitized)
        if sanitized != value:
            result.warnings.append("Removed control characters from artist name")
        
        # Strip whitespace
        sanitized = sanitized.strip()
        
        # Collapse multiple spaces
        sanitized = re.sub(r'\s+', ' ', sanitized)
        
        # ENFORCE: Single artist only - detect multi-artist delimiters
        multi_artist_indicators = [";", " / ", " & ", " and ", " feat", " ft.", " with "]
        for indicator in multi_artist_indicators:
            if indicator in sanitized.lower():
                result.valid = False
                result.errors.append(f"Multiple artists detected ('{indicator}'). Only single, atomic artists allowed.")
                break
        
        # Check for multiple commas (indicates multiple names, not "Last, First")
        comma_count = sanitized.count(",")
        if comma_count > 1:
            result.valid = False
            result.errors.append(f"Multiple artists detected (multiple commas). Only single, atomic artists allowed.")
        
        # Check length
        if len(sanitized) > cls.MAX_ARTIST_LENGTH:
            result.warnings.append(f"Artist name truncated from {len(sanitized)} to {cls.MAX_ARTIST_LENGTH} characters")
            sanitized = sanitized[:cls.MAX_ARTIST_LENGTH].strip()
        
        # Check for suspicious patterns
        if not sanitized:
            result.valid = False
            result.errors.append("Artist name is empty after sanitization")
        elif sanitized.lower() in ("unknown", "various", "various artists", "n/a", "null", "none"):
            result.valid = False
            result.errors.append(f"Generic/multi-artist name not allowed: {sanitized}. Must be a single, specific artist.")
        
        result.sanitized_value = sanitized if result.valid else None
        return result
    
    @classmethod
    def validate_album(cls, value: Optional[str]) -> ValidationResult:
        """
        Validate and sanitize album name.
        
        Rules:
        - Remove control characters
        - Normalize unicode
        - Check length
        - Remove leading/trailing whitespace
        """
        if not value:
            return ValidationResult(valid=True, sanitized_value="")
        
        result = ValidationResult(valid=True)
        sanitized = value
        
        # Normalize unicode
        sanitized = unicodedata.normalize("NFC", sanitized)
        
        # Remove control characters
        sanitized = cls._remove_control_characters(sanitized)
        if sanitized != value:
            result.warnings.append("Removed control characters from album name")
        
        # Strip whitespace
        sanitized = sanitized.strip()
        
        # Collapse multiple spaces
        sanitized = re.sub(r'\s+', ' ', sanitized)
        
        # Check length
        if len(sanitized) > cls.MAX_ALBUM_LENGTH:
            result.warnings.append(f"Album name truncated from {len(sanitized)} to {cls.MAX_ALBUM_LENGTH} characters")
            sanitized = sanitized[:cls.MAX_ALBUM_LENGTH].strip()
        
        # Check for suspicious patterns
        if not sanitized:
            result.valid = False
            result.errors.append("Album name is empty after sanitization")
        elif sanitized.lower() in ("unknown", "untitled", "n/a", "null", "none"):
            result.warnings.append(f"Generic album name detected: {sanitized}")
        
        result.sanitized_value = sanitized
        return result
    
    @classmethod
    def validate_title(cls, value: Optional[str]) -> ValidationResult:
        """
        Validate and sanitize track title.
        
        Rules:
        - Remove control characters
        - Normalize unicode
        - Check length
        - Remove leading/trailing whitespace
        """
        if not value:
            return ValidationResult(valid=False, errors=["Track title is required"])
        
        result = ValidationResult(valid=True)
        sanitized = value
        
        # Normalize unicode
        sanitized = unicodedata.normalize("NFC", sanitized)
        
        # Remove control characters
        sanitized = cls._remove_control_characters(sanitized)
        if sanitized != value:
            result.warnings.append("Removed control characters from track title")
        
        # Strip whitespace
        sanitized = sanitized.strip()
        
        # Collapse multiple spaces
        sanitized = re.sub(r'\s+', ' ', sanitized)
        
        # Check length
        if len(sanitized) > cls.MAX_TITLE_LENGTH:
            result.warnings.append(f"Track title truncated from {len(sanitized)} to {cls.MAX_TITLE_LENGTH} characters")
            sanitized = sanitized[:cls.MAX_TITLE_LENGTH].strip()
        
        # Check for suspicious patterns
        if not sanitized:
            result.valid = False
            result.errors.append("Track title is empty after sanitization")
        elif sanitized.lower() in ("untitled", "track", "n/a", "null", "none"):
            result.warnings.append(f"Generic track title detected: {sanitized}")
        
        result.sanitized_value = sanitized
        return result
    
    @classmethod
    def validate_genre(cls, value: Optional[str]) -> ValidationResult:
        """Validate and sanitize genre."""
        if not value:
            return ValidationResult(valid=True, sanitized_value="")
        
        result = ValidationResult(valid=True)
        sanitized = value
        
        # Normalize unicode
        sanitized = unicodedata.normalize("NFC", sanitized)
        
        # Remove control characters
        sanitized = cls._remove_control_characters(sanitized)
        
        # Strip whitespace
        sanitized = sanitized.strip()
        
        # Check length
        if len(sanitized) > cls.MAX_GENRE_LENGTH:
            result.warnings.append(f"Genre truncated from {len(sanitized)} to {cls.MAX_GENRE_LENGTH} characters")
            sanitized = sanitized[:cls.MAX_GENRE_LENGTH].strip()
        
        result.sanitized_value = sanitized
        return result
    
    @classmethod
    def validate_year(cls, value: Optional[int | str]) -> ValidationResult:
        """
        Validate year value.
        
        Rules:
        - Must be integer or parseable string
        - Must be between MIN_YEAR and MAX_YEAR
        - Can be None (unknown release date)
        """
        if value is None:
            return ValidationResult(valid=True, sanitized_value=None)
        
        result = ValidationResult(valid=True)
        
        # Try to parse if string
        if isinstance(value, str):
            # Extract first 4-digit year
            match = re.search(r'(19|20)\d{2}', value)
            if match:
                value = int(match.group(0))
            else:
                result.valid = False
                result.errors.append(f"Cannot parse year from: {value}")
                return result
        
        if not isinstance(value, int):
            result.valid = False
            result.errors.append(f"Year must be integer, got: {type(value).__name__}")
            return result
        
        # Check range
        if value < cls.MIN_YEAR:
            result.valid = False
            result.errors.append(f"Year {value} is too old (min: {cls.MIN_YEAR})")
        elif value > cls.MAX_YEAR:
            result.valid = False
            result.errors.append(f"Year {value} is in the future (max: {cls.MAX_YEAR})")
        
        result.sanitized_value = value if result.valid else None
        return result
    
    @classmethod
    def validate_track_number(cls, value: Optional[int | str]) -> ValidationResult:
        """
        Validate track number.
        
        Rules:
        - Must be positive integer
        - Must be <= MAX_TRACK_NUMBER
        - Can be None (unknown track number)
        """
        if value is None:
            return ValidationResult(valid=True, sanitized_value=None)
        
        result = ValidationResult(valid=True)
        
        # Try to parse if string
        if isinstance(value, str):
            # Handle "3/12" format (track 3 of 12)
            if "/" in value:
                value = value.split("/")[0].strip()
            try:
                value = int(value)
            except (ValueError, TypeError):
                result.valid = False
                result.errors.append(f"Cannot parse track number from: {value}")
                return result
        
        if not isinstance(value, int):
            result.valid = False
            result.errors.append(f"Track number must be integer, got: {type(value).__name__}")
            return result
        
        # Check range
        if value < 1:
            result.valid = False
            result.errors.append(f"Track number {value} must be positive")
        elif value > cls.MAX_TRACK_NUMBER:
            result.valid = False
            result.errors.append(f"Track number {value} exceeds maximum ({cls.MAX_TRACK_NUMBER})")
        
        result.sanitized_value = value if result.valid else None
        return result
    
    @classmethod
    def validate_disc_number(cls, value: Optional[int | str]) -> ValidationResult:
        """
        Validate disc number.
        
        Rules:
        - Must be positive integer
        - Must be <= MAX_DISC_NUMBER
        - Can be None (single disc or unknown)
        """
        if value is None:
            return ValidationResult(valid=True, sanitized_value=None)
        
        result = ValidationResult(valid=True)
        
        # Try to parse if string
        if isinstance(value, str):
            # Handle "2/3" format (disc 2 of 3)
            if "/" in value:
                value = value.split("/")[0].strip()
            try:
                value = int(value)
            except (ValueError, TypeError):
                result.valid = False
                result.errors.append(f"Cannot parse disc number from: {value}")
                return result
        
        if not isinstance(value, int):
            result.valid = False
            result.errors.append(f"Disc number must be integer, got: {type(value).__name__}")
            return result
        
        # Check range
        if value < 1:
            result.valid = False
            result.errors.append(f"Disc number {value} must be positive")
        elif value > cls.MAX_DISC_NUMBER:
            result.valid = False
            result.errors.append(f"Disc number {value} exceeds maximum ({cls.MAX_DISC_NUMBER})")
        
        result.sanitized_value = value if result.valid else None
        return result
    
    @classmethod
    def validate_duration(cls, value: Optional[int | float]) -> ValidationResult:
        """
        Validate track duration in seconds.
        
        Rules:
        - Must be positive number
        - Must be <= MAX_DURATION_SECONDS
        - Can be None (unknown duration)
        """
        if value is None:
            return ValidationResult(valid=True, sanitized_value=None)
        
        result = ValidationResult(valid=True)
        
        # Convert to int
        try:
            value = int(value)
        except (ValueError, TypeError):
            result.valid = False
            result.errors.append(f"Cannot parse duration from: {value}")
            return result
        
        # Check range
        if value <= 0:
            result.valid = False
            result.errors.append(f"Duration {value} must be positive")
        elif value > cls.MAX_DURATION_SECONDS:
            result.warnings.append(f"Duration {value}s exceeds typical maximum ({cls.MAX_DURATION_SECONDS}s)")
            # Don't invalidate - some classical pieces are very long
        
        result.sanitized_value = value if result.valid else None
        return result
    
    @classmethod
    def _remove_control_characters(cls, text: str) -> str:
        """Remove control characters from string."""
        return "".join(ch for ch in text if ord(ch) not in cls.CONTROL_CHARS)
    
    @classmethod
    def validate_metadata_complete(
        cls,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        title: Optional[str] = None,
        track_number: Optional[int] = None,
    ) -> ValidationResult:
        """
        Validate that metadata is sufficiently complete.
        
        At minimum, we need title and either (artist or album_artist).
        """
        result = ValidationResult(valid=True)
        
        if not title:
            result.valid = False
            result.errors.append("Track title is required")
        
        if not artist and not album:
            result.warnings.append("Neither artist nor album provided - track may be hard to organize")
        
        if track_number is not None and track_number <= 0:
            result.warnings.append(f"Invalid track number: {track_number}")
        
        return result


class IdempotencyChecker:
    """
    Ensures operations are idempotent - can be safely run multiple times.
    """
    
    @staticmethod
    def is_tag_write_needed(current_tags: dict, new_tags: dict) -> bool:
        """
        Check if writing new tags would actually change anything.
        
        Returns True if write is needed, False if tags are already correct.
        """
        for key, new_value in new_tags.items():
            current_value = current_tags.get(key)
            
            # Normalize for comparison
            if isinstance(new_value, str) and isinstance(current_value, str):
                if new_value.strip() != current_value.strip():
                    return True
            elif new_value != current_value:
                return True
        
        return False
    
    @staticmethod
    def is_move_needed(src: Path, dst: Path) -> bool:
        """
        Check if moving a file is actually needed.
        
        Returns False if source and destination are the same (already in correct location).
        """
        try:
            return src.resolve() != dst.resolve()
        except (OSError, FileNotFoundError):
            return True
    
    @staticmethod
    def normalize_path(path: Path) -> Path:
        """Normalize path for comparison."""
        try:
            return path.resolve()
        except (OSError, FileNotFoundError):
            return path
