from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import re
import shutil
import unicodedata
from pathlib import Path

from mutagen import File as MutagenFile

from .cache import MetadataCache
from .config import Settings, find_config
from .daemon import AudioMetaDaemon
from .heuristics import guess_metadata_from_path
from .providers.validation import validate_providers
from .scanner import LibraryScanner

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
    subparsers.add_parser("audit", help="Report directories containing mixed album/artist metadata")

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
    requires_providers = args.command in {"scan", "daemon"}
    if requires_providers:
        validate_providers(settings.providers)
    if args.clear_move_cache:
        cache = MetadataCache(settings.daemon.cache_path)
        cache.clear_moves()
        cache.close()
    daemon: AudioMetaDaemon | None = None
    if args.command in {"scan", "daemon"}:
        daemon = AudioMetaDaemon(
            settings,
            dry_run_output=args.dry_run_output,
            interactive=(args.command == "scan"),
            release_cache_enabled=not args.disable_release_cache,
        )

    try:
        match args.command:
            case "scan":
                asyncio.run(daemon.run_scan())
            case "daemon":
                asyncio.run(daemon.run_daemon())
            case "audit":
                audit_library(settings)
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


def audit_library(settings: Settings) -> None:
    scanner = LibraryScanner(settings.library)
    suspects: list[
        tuple[
            Path,
            dict[tuple[str, str], dict[str, object]],
            dict[str, dict[str, object]],
            bool,
        ]
    ] = []
    library_roots = [root.resolve() for root in settings.library.roots]
    for batch in scanner.iter_directories():
        combos: dict[tuple[str, str], dict[str, object]] = {}
        titles: dict[str, dict[str, object]] = {}
        for path in batch.files:
            tags = _read_basic_tags(path)
            guess = guess_metadata_from_path(path)
            artist = tags.get("albumartist") or tags.get("artist") or guess.artist
            album = tags.get("album") or guess.album
            norm_artist = _normalize_text(artist)
            norm_album = _normalize_text(album)
            key = (norm_artist or "unknown", norm_album or "unknown")
            bucket = combos.setdefault(
                key,
                {
                    "artist": artist or guess.artist or "Unknown Artist",
                    "album": album or guess.album or "Unknown Album",
                    "files": [],
                },
            )
            bucket["files"].append(path.name)
            raw_title = tags.get("title") or guess.title or path.stem
            norm_title = _normalize_text(raw_title)
            if norm_title:
                title_bucket = titles.setdefault(
                    norm_title,
                    {
                        "title": raw_title or path.stem,
                        "files": [],
                    },
                )
                title_bucket["files"].append(path.name)
        if not combos:
            continue
        known_artists = {key[0] for key in combos if key[0] != "unknown"}
        known_albums = {key[1] for key in combos if key[1] != "unknown"}
        duplicate_titles = {key: info for key, info in titles.items() if len(info["files"]) > 1}
        multi_combo = len(known_artists) > 1 or len(known_albums) > 1
        if not multi_combo and not duplicate_titles:
            continue
        suspects.append((batch.directory, combos, duplicate_titles, multi_combo))
    if not suspects:
        print("No directories with mixed album, artist, or duplicate track metadata detected.")
        return
    suspects.sort(key=lambda entry: len(entry[1]), reverse=True)
    print(f"Found {len(suspects)} directory/directories needing review:\n")
    for directory, combos, duplicate_titles, multi_combo in suspects:
        rel = _display_relative(directory, library_roots)
        print(f"- {rel}")
        if multi_combo:
            print(f"    Multiple album/artist combinations ({len(combos)}):")
            for (norm_artist, norm_album), info in sorted(combos.items()):
                files: list[str] = info["files"]  # type: ignore[assignment]
                sample = ", ".join(sorted(files)[:3])
                extra = max(0, len(files) - 3)
                if extra:
                    sample = f"{sample}, +{extra} more"
                artist_label = info["artist"]  # type: ignore[index]
                album_label = info["album"]  # type: ignore[index]
                print(f"      • {artist_label} – {album_label} ({len(files)} tracks): {sample}")
        if duplicate_titles:
            print("    Duplicate track titles detected:")
            for key, info in sorted(duplicate_titles.items()):
                files: list[str] = info["files"]  # type: ignore[assignment]
                sample = ", ".join(sorted(files)[:3])
                extra = max(0, len(files) - 3)
                if extra:
                    sample = f"{sample}, +{extra} more"
                title_label = info["title"]  # type: ignore[index]
                print(f"      • {title_label} ({len(files)} copies): {sample}")
        print()


def _display_relative(path: Path, roots: list[Path]) -> str:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path
    for root in roots:
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return str(path)


def _read_basic_tags(path: Path) -> dict[str, str | None]:
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return {}
    if not audio or not audio.tags:
        return {}
    def first(keys: list[str]) -> str | None:
        for key in keys:
            value = audio.tags.get(key)
            if value:
                if isinstance(value, list):
                    return str(value[0])
                return str(value)
        return None
    return {
        "artist": first(["artist"]),
        "albumartist": first(["albumartist", "album artist"]),
        "album": first(["album"]),
    }


def _normalize_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = unicodedata.normalize("NFKD", value)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    return cleaned or None
