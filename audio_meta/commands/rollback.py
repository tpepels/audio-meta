from __future__ import annotations

import errno
import logging
import shutil
from pathlib import Path

from ..cache import MetadataCache
from ..fs_utils import fit_destination_path, path_exists, safe_rename


def run(cache: MetadataCache) -> None:
    moves = cache.list_moves()
    if not moves:
        print("No recorded moves to rollback.")
        cache.clear_directory_releases()
        return
    print(f"Rolling back {len(moves)} recorded move(s)...")
    restored = 0
    failed = 0
    for source_str, target_str in moves:
        source_entry = source_str
        source = Path(source_str)
        target = Path(target_str)
        target_exists = path_exists(target)
        if target_exists is None:
            logging.warning("Cannot restore %s -> %s: parent missing", target, source)
            cache.delete_move(source_entry)
            failed += 1
            continue
        if not target_exists:
            logging.warning("Cannot restore %s -> %s: target missing", target, source)
            cache.delete_move(source_entry)
            failed += 1
            continue
        fitted_source = fit_destination_path(source)
        if fitted_source != source:
            logging.info(
                "Truncating restore path %s -> %s", source.name, fitted_source.name
            )
            source = fitted_source
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                safe_rename(target, source)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.move(str(target), str(source))
            stat = source.stat()
            cache.set_processed_file(
                source, stat.st_mtime_ns, stat.st_size, organized=False
            )
            cache.delete_move(source_entry)
            restored += 1
            logging.info("Restored %s -> %s", target, source)
        except Exception as exc:  # pragma: no cover - best effort
            logging.error("Failed to restore %s -> %s: %s", target, source, exc)
            failed += 1
    cache.clear_directory_releases()
    print(f"Rollback complete: {restored} restored, {failed} failed.")
