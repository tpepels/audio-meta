"""
Determinism diagnostics and enforcement for the matching pipeline.

Investigates and fixes root causes of repeated re-matching prompts when
running consecutive scans on an unchanged library.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from .cache import MetadataCache

logger = logging.getLogger(__name__)


@dataclass
class DeterminismCheck:
    """Result of a determinism check."""
    is_stable: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class DeterminismChecker:
    """
    Checks and enforces determinism in the matching pipeline.
    
    Common causes of non-deterministic behavior:
    1. Non-canonical artist names (different spelling between scans)
    2. Floating confidence thresholds not being persisted
    3. Directory/file ordering instability
    4. Transient IDs from providers
    5. Hash/checksum mismatches due to file metadata changes
    """
    
    def __init__(self, cache: MetadataCache) -> None:
        self.cache = cache
    
    def check_directory_stability(
        self,
        directory: Path,
        current_hash: str,
        current_release_id: Optional[str],
    ) -> DeterminismCheck:
        """
        Check if a directory's match should be stable between scans.
        
        Returns DeterminismCheck with any issues that could cause instability.
        """
        issues = []
        warnings = []
        diagnostics: dict[str, Any] = {}
        
        # 1. Check if directory hash matches cached hash
        cached_hash = self.cache.get_directory_hash(directory)
        if cached_hash:
            if cached_hash != current_hash:
                issues.append(
                    f"Directory hash changed: {cached_hash[:8]}... -> {current_hash[:8]}..."
                )
                diagnostics["hash_changed"] = True
        
        # 2. Check if release decision is cached
        cached_release = self.cache.get_directory_release(directory)
        if cached_release:
            cached_provider, cached_id, cached_score = cached_release
            
            # Check if release ID changed
            if current_release_id and cached_id != current_release_id:
                issues.append(
                    f"Release ID mismatch: cached={cached_id}, current={current_release_id}"
                )
                diagnostics["release_id_changed"] = True
            
            diagnostics["cached_release"] = {
                "provider": cached_provider,
                "id": cached_id,
                "score": cached_score,
            }
        else:
            warnings.append("No cached release decision - will prompt on next scan")
        
        # 3. Check hash-based release lookup
        if current_hash:
            hash_release = self.cache.get_release_by_hash(current_hash)
            if hash_release:
                hash_provider, hash_id, hash_score = hash_release
                diagnostics["hash_release"] = {
                    "provider": hash_provider,
                    "id": hash_id,
                    "score": hash_score,
                }
                
                # Warn if hash release differs from cached release
                if cached_release:
                    if cached_id != hash_id:
                        warnings.append(
                            f"Hash-based release ({hash_id}) differs from directory release ({cached_id})"
                        )
        
        # 4. Check if directory is in deferred prompts
        deferred = self.cache.list_deferred_prompts()
        for deferred_path, reason in deferred:
            if Path(deferred_path) == directory:
                warnings.append(f"Directory is in deferred prompt queue: {reason}")
                diagnostics["deferred"] = reason
                break
        
        is_stable = len(issues) == 0
        
        return DeterminismCheck(
            is_stable=is_stable,
            issues=issues,
            warnings=warnings,
            diagnostics=diagnostics,
        )
    
    def compute_content_hash(self, directory: Path, extensions: set[str]) -> str:
        """
        Compute a stable content hash for a directory.
        
        This hash is based on:
        - Sorted list of audio filenames (not full paths)
        - File sizes
        - NOT file modification times (to allow metadata-only changes)
        """
        hasher = hashlib.sha256()
        
        try:
            files = []
            for item in directory.iterdir():
                if item.is_file() and item.suffix.lower() in extensions:
                    stat = item.stat()
                    files.append((item.name, stat.st_size))
            
            # Sort for determinism
            files.sort()
            
            for name, size in files:
                hasher.update(name.encode("utf-8"))
                hasher.update(str(size).encode("utf-8"))
        except (PermissionError, OSError) as exc:
            logger.debug("Failed to compute content hash for %s: %s", directory, exc)
            return ""
        
        return hasher.hexdigest()
    
    def ensure_decision_persisted(
        self,
        directory: Path,
        provider: str,
        release_id: str,
        score: float,
        directory_hash: Optional[str] = None,
    ) -> None:
        """
        Ensure a release decision is properly persisted in all lookup tables.
        
        This helps prevent re-matching on subsequent scans.
        """
        # Persist directory -> release mapping
        self.cache.set_directory_release(directory, provider, release_id, score)
        
        # Persist hash -> release mapping if we have a hash
        if directory_hash:
            self.cache.set_directory_hash(directory, directory_hash)
            self.cache.set_release_by_hash(directory_hash, provider, release_id, score)
        
        logger.debug(
            "Persisted release decision for %s: %s:%s (score=%.2f)",
            directory,
            provider,
            release_id,
            score,
        )
    
    def record_match_attempt(
        self,
        directory: Path,
        *,
        provider: Optional[str],
        release_id: Optional[str],
        score: float,
        directory_hash: Optional[str],
        was_prompted: bool,
        outcome: str,
    ) -> None:
        """
        Record a match attempt for debugging determinism issues.
        
        This creates an audit trail that can be analyzed to find patterns.
        """
        payload = {
            "directory": str(directory),
            "provider": provider,
            "release_id": release_id,
            "score": score,
            "directory_hash": directory_hash[:16] if directory_hash else None,
            "was_prompted": was_prompted,
            "outcome": outcome,
        }
        
        self.cache.append_audit_event("match_attempt", payload)


@dataclass
class StabilityReport:
    """Report on scan stability between runs."""
    total_directories: int = 0
    stable_directories: int = 0
    unstable_directories: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def stability_ratio(self) -> float:
        if self.total_directories == 0:
            return 1.0
        return self.stable_directories / self.total_directories


def generate_stability_report(
    cache: MetadataCache,
    library_roots: list[Path],
    extensions: set[str],
) -> StabilityReport:
    """
    Generate a report on matching stability across the library.
    
    This helps identify directories that might cause re-prompting.
    """
    import os
    
    report = StabilityReport()
    checker = DeterminismChecker(cache)
    
    for root in library_roots:
        if not root.exists():
            continue
        
        for dirpath, _, filenames in os.walk(root):
            directory = Path(dirpath)
            
            # Skip directories without audio files
            audio_files = [f for f in filenames if Path(f).suffix.lower() in extensions]
            if not audio_files:
                continue
            
            report.total_directories += 1
            
            # Compute current hash
            current_hash = checker.compute_content_hash(directory, extensions)
            
            # Get cached release
            cached = cache.get_directory_release(directory)
            current_release_id = cached[1] if cached else None
            
            # Check stability
            check = checker.check_directory_stability(
                directory, current_hash, current_release_id
            )
            
            if check.is_stable:
                report.stable_directories += 1
            else:
                report.unstable_directories += 1
                report.issues.append({
                    "directory": str(directory),
                    "issues": check.issues,
                    "warnings": check.warnings,
                })
    
    return report


def print_stability_report(report: StabilityReport) -> None:
    """Print a human-readable stability report."""
    print("\n=== Scan Stability Report ===\n")
    print(f"Total directories: {report.total_directories}")
    print(f"Stable directories: {report.stable_directories}")
    print(f"Unstable directories: {report.unstable_directories}")
    print(f"Stability ratio: {report.stability_ratio:.1%}")
    
    if report.issues:
        print("\n--- Stability Issues ---")
        for item in report.issues[:20]:
            print(f"\n  {item['directory']}")
            for issue in item.get("issues", []):
                print(f"    - ISSUE: {issue}")
            for warning in item.get("warnings", []):
                print(f"    - WARNING: {warning}")
        
        if len(report.issues) > 20:
            print(f"\n  ... and {len(report.issues) - 20} more")


class ScanStateTracker:
    """
    Tracks scan state to detect and prevent non-deterministic behavior.
    
    This class should be used by the daemon to ensure:
    1. Processed directories are properly marked
    2. Release decisions are consistently persisted
    3. Hash-based lookups work correctly
    """
    
    def __init__(self, cache: MetadataCache) -> None:
        self.cache = cache
        self._processed_this_scan: set[str] = set()
        self._match_decisions: dict[str, tuple[str, str, float]] = {}
    
    def mark_processed(
        self,
        directory: Path,
        *,
        provider: Optional[str] = None,
        release_id: Optional[str] = None,
        score: float = 0.0,
        directory_hash: Optional[str] = None,
    ) -> None:
        """Mark a directory as processed in this scan."""
        dir_str = str(directory)
        self._processed_this_scan.add(dir_str)
        
        if provider and release_id:
            self._match_decisions[dir_str] = (provider, release_id, score)
            
            # Ensure persistence
            checker = DeterminismChecker(self.cache)
            checker.ensure_decision_persisted(
                directory, provider, release_id, score, directory_hash
            )
    
    def was_processed_this_scan(self, directory: Path) -> bool:
        """Check if a directory was already processed in this scan."""
        return str(directory) in self._processed_this_scan
    
    def get_scan_statistics(self) -> dict[str, Any]:
        """Get statistics about this scan."""
        return {
            "processed_count": len(self._processed_this_scan),
            "match_count": len(self._match_decisions),
        }
    
    def should_reprocess(
        self,
        directory: Path,
        current_hash: Optional[str],
    ) -> tuple[bool, str]:
        """
        Determine if a directory should be reprocessed.
        
        Returns (should_reprocess, reason).
        """
        # Already processed this scan
        if self.was_processed_this_scan(directory):
            return False, "already_processed_this_scan"
        
        # Check cached release
        cached = self.cache.get_directory_release(directory)
        if not cached:
            return True, "no_cached_release"
        
        # Check hash stability
        if current_hash:
            cached_hash = self.cache.get_directory_hash(directory)
            if cached_hash and cached_hash != current_hash:
                return True, "hash_changed"
        
        # Check if it's in the deferred queue
        deferred = self.cache.list_deferred_prompts()
        for deferred_path, _ in deferred:
            if Path(deferred_path) == directory:
                return True, "deferred_prompt"
        
        # Check if files are marked as organized
        # (This would need integration with processed_files tracking)
        
        return False, "cached_decision_valid"
