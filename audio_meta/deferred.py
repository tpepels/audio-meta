from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def schedule_directory(daemon: Any, directory: Path, reason: str) -> None:
    if not daemon.defer_prompts:
        return
    if not daemon.interactive and not daemon._processing_deferred:
        with daemon._defer_lock:
            if directory in daemon._deferred_set:
                return
            daemon._deferred_set.add(directory)
        daemon.cache.add_deferred_prompt(directory, reason)
        logger.info("Deferring %s (%s); will request input later", daemon._display_path(directory), reason)
        return
    with daemon._defer_lock:
        if directory in daemon._deferred_set:
            return
        daemon._deferred_set.add(directory)
        daemon._deferred_directories.append(directory)
    daemon.cache.add_deferred_prompt(directory, reason)
    logger.info("Deferring %s (%s); will request input later", daemon._display_path(directory), reason)


def sync_from_cache(daemon: Any) -> None:
    if not daemon.defer_prompts:
        return
    for directory_str, _reason in daemon.cache.list_deferred_prompts():
        path = Path(directory_str)
        with daemon._defer_lock:
            if path in daemon._deferred_set:
                continue
            daemon._deferred_set.add(path)
            daemon._deferred_directories.append(path)


def process_pending(daemon: Any) -> None:
    if not daemon.defer_prompts:
        return
    sync_from_cache(daemon)
    with daemon._defer_lock:
        pending = list(daemon._deferred_directories)
        daemon._deferred_directories.clear()
        daemon._deferred_set.clear()
    if not pending:
        return
    suffix = "ies" if len(pending) != 1 else "y"
    logger.info("Processing %d deferred directory%s...", len(pending), suffix)
    daemon._processing_deferred = True
    try:
        for directory in pending:
            batch = daemon.scanner.collect_directory(directory)
            if not batch:
                logger.warning("Deferred directory %s no longer exists; skipping", daemon._display_path(directory))
                daemon.cache.remove_deferred_prompt(directory)
                continue
            daemon._process_directory(batch, force_prompt=True)
            daemon.cache.remove_deferred_prompt(directory)
    finally:
        daemon._processing_deferred = False

