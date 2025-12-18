from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from watchdog.observers import Observer

from ..scanner import DirectoryBatch
from ..watchdog_handler import WatchHandler

if TYPE_CHECKING:  # pragma: no cover
    from .core import AudioMetaDaemon

logger = logging.getLogger(__name__)


def bootstrap_watchdog(daemon: "AudioMetaDaemon", loop: asyncio.AbstractEventLoop) -> None:
    handler = WatchHandler(
        daemon.queue,
        daemon.settings.library.include_extensions,
        daemon.scanner,
        loop=loop,
    )
    observer = Observer()
    for root in daemon.settings.library.roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    daemon.observer = observer


def start_workers(daemon: "AudioMetaDaemon") -> list[asyncio.Task[None]]:
    concurrency = 1 if daemon.interactive else daemon.settings.daemon.worker_concurrency
    return [asyncio.create_task(worker(daemon, i)) for i in range(concurrency)]


async def stop_workers(workers: list[asyncio.Task[None]]) -> None:
    for worker_task in workers:
        worker_task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


async def worker(daemon: "AudioMetaDaemon", worker_id: int) -> None:
    while True:
        batch: DirectoryBatch = await daemon.queue.get()
        try:
            if daemon.cache.is_directory_ignored(batch.directory):
                logger.info(
                    "Skipping ignored directory %s",
                    daemon._display_path(batch.directory),
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None, daemon._process_directory, batch
                )
        except Exception:  # pragma: no cover - logged and ignored
            logger.exception(
                "Worker %s failed to process %s", worker_id, batch.directory
            )
        finally:
            daemon.queue.task_done()

