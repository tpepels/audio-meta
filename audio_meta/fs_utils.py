from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Optional

ELLIPSIS = "…"
MAX_BASENAME_BYTES = 255


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
