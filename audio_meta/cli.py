from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from .audit import LibraryAuditor
from .cache import MetadataCache
from .config import Settings, find_config
from .daemon import AudioMetaDaemon
from .providers.validation import validate_providers

LOG_FORMAT = "%(levelname).1s | %(name)s | %(message)s"

C_RESET = "\033[0m"
LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # Cyan
    logging.INFO: "\033[37m",  # Light gray
    logging.WARNING: "\033[33m",  # Yellow
    logging.ERROR: "\033[31m",  # Red
    logging.CRITICAL: "\033[35m",  # Magenta
}


class ShortPathFormatter(logging.Formatter):
    def __init__(self, fmt: str, roots: list[Path]) -> None:
        super().__init__(fmt)
        self.roots = [str(root) for root in roots if root]

    def _shorten(self, message: str) -> str:
        for root in self.roots:
            if not root:
                continue
            if not message:
                break
            message = message.replace(f"{root}/", "")
            message = message.replace(root, "")
        return message

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return self._shorten(message)


class ColorFormatter(ShortPathFormatter):
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
        "--disable-release-cache",
        action="store_true",
        help="Do not reuse previously chosen releases during this run",
    )
    parser.add_argument(
        "--dry-run-output",
        type=Path,
        help="Record proposed tag changes to this file (JSON Lines) without editing files",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Run a one-off scan")
    subparsers.add_parser("daemon", help="Start the watchdog daemon")
    subparsers.add_parser("run", help="Run a scan followed by an audit with automatic fixes")
    audit_parser = subparsers.add_parser("audit", help="Report directories containing mixed album/artist metadata")
    audit_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically move files whose tags indicate a different artist/album",
    )
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Remove directories that only contain non-audio files"
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report directories that would be deleted",
    )
    subparsers.add_parser(
        "singletons",
        help="Interactively review directories that contain a single audio file",
    )

    args = parser.parse_args()
    config_path = find_config(args.config)
    settings = Settings.load(config_path)

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    display_roots = [root.resolve() for root in settings.library.roots]

    color_handler = logging.StreamHandler()
    color_handler.setFormatter(ColorFormatter(LOG_FORMAT, display_roots))
    root_logger.addHandler(color_handler)

    warn_buffer = WarningBufferHandler()
    warn_buffer.setFormatter(ShortPathFormatter(LOG_FORMAT, display_roots))
    root_logger.addHandler(warn_buffer)

    warn_log_path = Path.cwd() / "audio-meta-warnings.log"
    file_handler = logging.FileHandler(warn_log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(ShortPathFormatter(LOG_FORMAT, display_roots))
    root_logger.addHandler(file_handler)

    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
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
    requires_providers = args.command in {"scan", "daemon", "run"}
    if requires_providers:
        validate_providers(settings.providers)
    if args.clear_move_cache:
        cache = MetadataCache(settings.daemon.cache_path)
        cache.clear_moves()
        cache.close()
    daemon: AudioMetaDaemon | None = None
    if args.command in {"scan", "daemon", "run"}:
        daemon = AudioMetaDaemon(
            settings,
            dry_run_output=args.dry_run_output,
            interactive=(args.command in {"scan", "run"}),
            release_cache_enabled=not args.disable_release_cache,
        )

    try:
        match args.command:
            case "scan":
                asyncio.run(daemon.run_scan())
            case "daemon":
                asyncio.run(daemon.run_daemon())
            case "run":
                asyncio.run(daemon.run_scan())
                audit_library(settings, fix=True)
            case "audit":
                audit_library(settings, fix=getattr(args, "fix", False))
            case "cleanup":
                cleanup_directories(settings, dry_run=getattr(args, "dry_run", False))
            case "singletons":
                review_singletons(settings)
            case _:
                parser.error("Unknown command")
    finally:
        if daemon:
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
        source_entry = source_str
        source = Path(source_str)
        target = Path(target_str)
        target_exists = _path_exists(target)
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
        fitted_source = _fit_destination_path(source)
        if fitted_source != source:
            logging.info("Truncating restore path %s -> %s", source.name, fitted_source.name)
            source = fitted_source
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                _safe_rename(target, source)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.move(str(target), str(source))
            stat = source.stat()
            cache.set_processed_file(source, stat.st_mtime_ns, stat.st_size, organized=False)
            cache.delete_move(source_entry)
            restored += 1
            logging.info("Restored %s -> %s", target, source)
        except Exception as exc:  # pragma: no cover - best effort
            logging.error("Failed to restore %s -> %s: %s", target, source, exc)
            failed += 1
    cache.clear_directory_releases()
    cache.close()
    print(f"Rollback complete: {restored} restored, {failed} failed.")


def audit_library(settings: Settings, fix: bool = False) -> None:
    """Run the tag-based relocation audit, optionally auto-fixing misplaced files."""
    cache = MetadataCache(settings.daemon.cache_path)
    try:
        LibraryAuditor(settings, cache=cache).run(fix=fix)
    finally:
        cache.close()


def cleanup_directories(settings: Settings, dry_run: bool = False) -> None:
    audio_exts = {ext.lower() for ext in settings.library.include_extensions}
    roots = [root.resolve() for root in settings.library.roots]
    removed_dirs = 0
    removed_files = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
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


def review_singletons(settings: Settings) -> None:
    cache = MetadataCache(settings.daemon.cache_path)
    try:
        auditor = LibraryAuditor(settings, cache=cache)
        entries = auditor.collect_singletons()
        entries = [entry for entry in entries if entry.file_path.exists()]
        if not entries:
            print("No single-file directories detected.")
            return
        total = len(entries)
        for idx, entry in enumerate(entries, 1):
            directory_label = auditor.display_path(entry.directory)
            print(f"\n[{idx}/{total}] {directory_label}")
            print(f"    File: {entry.file_path.name}")
            print(f"    Artist: {entry.meta.artist or '<unknown>'}")
            print(f"    Album: {entry.meta.album or '<unknown>'}")
            if entry.meta.composer:
                print(f"    Composer: {entry.meta.composer}")
            print(f"    Title: {entry.meta.title or '<unknown>'}")
            if entry.meta.musicbrainz_release_id:
                print(f"    Release ID: {entry.meta.musicbrainz_release_id}")
            if entry.target:
                target_label = auditor.display_path(entry.target.parent)
                print(f"    Suggested target: {target_label}/{entry.target.name}")
            else:
                print("    Suggested target: (already in place or unknown)")
            while True:
                choice = input("Action [k]eep/[m]ove/[d]elete/[i]gnore/[q]uit: ").strip().lower()
                if choice in {"", "k"}:
                    break
                if choice == "q":
                    print("Stopping singleton review.")
                    return
                if choice == "m":
                    if not entry.target:
                        print("No suggested destination; keeping file in place.")
                        break
                    auditor.organizer.move(entry.meta, entry.target, dry_run=False)
                    auditor.organizer.cleanup_source_directory(entry.directory)
                    entry.file_path = entry.meta.path
                    print("Moved to", auditor.display_path(entry.meta.path.parent))
                    break
                if choice == "d":
                    try:
                        entry.file_path.unlink()
                        try:
                            entry.directory.rmdir()
                        except OSError:
                            pass
                        print("Deleted file (and directory if empty).")
                    except FileNotFoundError:
                        print("File already missing.")
                    break
                if choice == "i":
                    cache.ignore_directory(entry.directory, "user ignored singleton")
                    print("Directory will be ignored in future single-file audits.")
                    break
                print("Invalid choice. Use k/m/d/i/q.")
    finally:
        cache.close()


ELLIPSIS = "…"
MAX_BASENAME_BYTES = 255


def _directory_has_audio_files(path: Path, extensions: set[str]) -> bool:
    if not extensions:
        return True
    for root, _, files in os.walk(path):
        for name in files:
            if Path(name).suffix.lower() in extensions:
                return True
    return False


def _path_exists(path: Path) -> Optional[bool]:
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


def _fit_destination_path(path: Path) -> Path:
    name_bytes = path.name.encode("utf-8")
    if len(name_bytes) <= MAX_BASENAME_BYTES:
        return path
    suffix_bytes = path.suffix.encode("utf-8")
    ellipsis_bytes = ELLIPSIS.encode("utf-8")
    allowed = MAX_BASENAME_BYTES - len(suffix_bytes) - len(ellipsis_bytes)
    if allowed < 0:
        allowed = 0
    stem = path.stem or "file"
    truncated = stem.encode("utf-8")[:allowed].decode("utf-8", errors="ignore") or "file"
    candidate = path.with_name(f"{truncated}{ELLIPSIS}{path.suffix}")
    counter = 1
    while candidate.exists():
        extra = f"_{counter}"
        extra_bytes = extra.encode("utf-8")
        allowed = MAX_BASENAME_BYTES - len(suffix_bytes) - len(ellipsis_bytes) - len(extra_bytes)
        allowed = max(0, allowed)
        truncated = stem.encode("utf-8")[:allowed].decode("utf-8", errors="ignore") or f"file{counter}"
        candidate = path.with_name(f"{truncated}{extra}{ELLIPSIS}{path.suffix}")
        counter += 1
    return candidate


def _safe_rename(src: Path, dst: Path) -> None:
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
                    return None
        path.rmdir()
        return count
    except OSError as exc:
        logging.warning("Failed to remove %s: %s", path, exc)
        return None
