from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..config import Settings


def run(settings: Settings, *, dry_run: bool = False) -> None:
    audio_exts = {ext.lower() for ext in settings.library.include_extensions}
    roots = [root.resolve() for root in settings.library.roots]
    removed_dirs = 0
    removed_files = 0
    for root in roots:
        for dirpath, _, filenames in os.walk(root, topdown=False):
            path = Path(dirpath)
            if path in roots:
                continue
            if _directory_has_audio_files(path, audio_exts):
                continue
            if dry_run:
                print(f"[dry-run] Would remove {path} ({len(filenames)} files)")
                continue
            removed = _remove_tree(path)
            if removed is None:
                continue
            removed_dirs += 1
            removed_files += removed
            print(f"Removed {path}")
    suffix = " (dry-run)" if dry_run else ""
    print(
        f"Cleanup complete{suffix}: removed {removed_dirs} directories containing only non-audio files."
    )
    if not dry_run:
        print(f"Deleted {removed_files} files with non-audio content.")


def _directory_has_audio_files(path: Path, extensions: set[str]) -> bool:
    if not extensions:
        return True
    for root, _, files in os.walk(path):
        for name in files:
            if Path(name).suffix.lower() in extensions:
                return True
    return False


def _remove_tree(path: Path) -> Optional[int]:
    count = 0
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            root_path = Path(root)
            for name in files:
                try:
                    (root_path / name).unlink()
                    count += 1
                except FileNotFoundError:
                    continue
            for name in dirs:
                try:
                    (root_path / name).rmdir()
                except OSError:
                    continue
        path.rmdir()
        return count
    except Exception:  # pragma: no cover
        return None
