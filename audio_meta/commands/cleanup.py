from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Optional

from ..config import Settings

ELLIPSIS = "…"
MAX_BASENAME_BYTES = 255


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


def path_exists(path: Path) -> Optional[bool]:
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        if exc.errno != errno.ENAMETOOLONG:
            raise
        parent = path.parent
        try:
            with os.scandir(parent) as it:
                for entry in it:
                    if entry.name == path.name:
                        return True
        except FileNotFoundError:
            return None
        return False


def fit_destination_path(path: Path) -> Path:
    name_bytes = path.name.encode("utf-8")
    if len(name_bytes) <= MAX_BASENAME_BYTES:
        return path
    suffix_bytes = path.suffix.encode("utf-8")
    ellipsis_bytes = ELLIPSIS.encode("utf-8")
    allowed = MAX_BASENAME_BYTES - len(suffix_bytes) - len(ellipsis_bytes)
    if allowed < 0:
        allowed = 0
    stem = path.stem or "file"
    truncated = (
        stem.encode("utf-8")[:allowed].decode("utf-8", errors="ignore") or "file"
    )
    candidate = path.with_name(f"{truncated}{ELLIPSIS}{path.suffix}")
    counter = 1
    while candidate.exists():
        extra = f"_{counter}"
        extra_bytes = extra.encode("utf-8")
        allowed = (
            MAX_BASENAME_BYTES
            - len(suffix_bytes)
            - len(ellipsis_bytes)
            - len(extra_bytes)
        )
        allowed = max(0, allowed)
        truncated = (
            stem.encode("utf-8")[:allowed].decode("utf-8", errors="ignore")
            or f"file{counter}"
        )
        candidate = path.with_name(f"{truncated}{extra}{ELLIPSIS}{path.suffix}")
        counter += 1
    return candidate


def safe_rename(src: Path, dst: Path) -> None:
    try:
        src.rename(dst)
        return
    except OSError as exc:
        if exc.errno != errno.ENAMETOOLONG:
            raise
    src_dir_fd = os.open(src.parent, os.O_RDONLY)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst_dir_fd = os.open(dst.parent, os.O_RDONLY)
        try:
            os.rename(src.name, dst.name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        finally:
            os.close(dst_dir_fd)
    finally:
        os.close(src_dir_fd)


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
