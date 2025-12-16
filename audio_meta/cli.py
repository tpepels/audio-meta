from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import errno
import shutil

from .config import Settings, find_config
from .daemon import AudioMetaDaemon
from .providers.validation import validate_providers
from .cache import MetadataCache

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

C_RESET = "\033[0m"
LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # Cyan
    logging.INFO: "\033[37m",  # Light gray
    logging.WARNING: "\033[33m",  # Yellow
    logging.ERROR: "\033[31m",  # Red
    logging.CRITICAL: "\033[35m",  # Magenta
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = LEVEL_COLORS.get(record.levelno)
        if not color:
            return message
        return f"{color}{message}{C_RESET}"


class WarningBufferHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover
            msg = record.getMessage()
        self.records.append(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audio metadata correction")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    parser.add_argument(
        "--clear-move-cache",
        action="store_true",
        help="Clear recorded move history before running",
    )
    parser.add_argument(
        "--rollback-moves",
        action="store_true",
        help="Move files back to their original locations using recorded move history, then exit",
    )
    parser.add_argument(
        "--reset-release-cache",
        action="store_true",
        help="Clear stored directory release choices (does not touch provider caches)",
    )
    parser.add_argument(
        "--dry-run-output",
        type=Path,
        help="Record proposed tag changes to this file (JSON Lines) without editing files",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Run a one-off scan")
    subparsers.add_parser("daemon", help="Start the watchdog daemon")

    args = parser.parse_args()
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    color_handler = logging.StreamHandler()
    color_handler.setFormatter(ColorFormatter(LOG_FORMAT))
    root_logger.addHandler(color_handler)

    warn_buffer = WarningBufferHandler()
    warn_buffer.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(warn_buffer)

    warn_log_path = Path.cwd() / "audio-meta-warnings.log"
    file_handler = logging.FileHandler(warn_log_path, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    config_path = find_config(args.config)
    settings = Settings.load(config_path)
    if args.reset_release_cache:
        cache = MetadataCache(settings.daemon.cache_path)
        cache.clear_directory_releases()
        cache.close()
        print("Cleared stored release selections.")
        if args.command is None:
            return
    if args.rollback_moves:
        rollback_moves(settings)
        return
    validate_providers(settings.providers)
    if args.clear_move_cache:
        cache = MetadataCache(settings.daemon.cache_path)
        cache.clear_moves()
        cache.close()
    daemon = AudioMetaDaemon(settings, dry_run_output=args.dry_run_output, interactive=(args.command == "scan"))

    try:
        match args.command:
            case "scan":
                asyncio.run(daemon.run_scan())
            case "daemon":
                asyncio.run(daemon.run_daemon())
            case _:
                parser.error("Unknown command")
    finally:
        daemon.report_skips()
        if warn_buffer.records:
            print("\n\033[33mWarnings/Errors summary:\033[0m")
            for line in warn_buffer.records:
                print(f" - {line}")
            print(f"\nFull warning log: {warn_log_path}")


def rollback_moves(settings: Settings) -> None:
    cache = MetadataCache(settings.daemon.cache_path)
    moves = cache.list_moves()
    if not moves:
        print("No recorded moves to rollback.")
        cache.clear_directory_releases()
        cache.close()
        return
    print(f"Rolling back {len(moves)} recorded move(s)...")
    restored = 0
    failed = 0
    for source_str, target_str in moves:
        source = Path(source_str)
        target = Path(target_str)
        if not target.exists():
            logging.warning("Cannot restore %s -> %s: target missing", target, source)
            failed += 1
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                target.rename(source)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.move(str(target), str(source))
            stat = source.stat()
            cache.set_processed_file(source, stat.st_mtime_ns, stat.st_size, organized=False)
            cache.delete_move(source)
            restored += 1
            logging.info("Restored %s -> %s", target, source)
        except Exception as exc:  # pragma: no cover - best effort
            logging.error("Failed to restore %s -> %s: %s", target, source, exc)
            failed += 1
    cache.clear_directory_releases()
    cache.close()
    print(f"Rollback complete: {restored} restored, {failed} failed.")
