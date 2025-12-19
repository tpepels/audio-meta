from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .app import AudioMetaApp
from .cache import MetadataCache
from .commands import audit_events as cmd_audit_events
from .commands import audit_run as cmd_audit_run
from .commands import cleanup as cmd_cleanup
from .commands import doctor as cmd_doctor
from .commands import export_testcase as cmd_export_testcase
from .commands import rollback as cmd_rollback
from .commands import singletons as cmd_singletons
from .config import Settings, find_config
from .determinism import generate_stability_report, print_stability_report
from .identity import run_prescan, print_identity_report
from .daemon import AudioMetaDaemon
from .providers.validation import validate_providers

LOGO = r"""
██████╗ ███████╗███████╗ ██████╗ ███╗   ██╗ █████╗ ███╗   ██╗ ██████╗███████╗
██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║██╔══██╗████╗  ██║██╔════╝██╔════╝
██████╔╝█████╗  ███████╗██║   ██║██╔██╗ ██║███████║██╔██╗ ██║██║     █████╗  
██╔══██╗██╔══╝  ╚════██║██║   ██║██║╚██╗██║██╔══██║██║╚██╗██║██║     ██╔══╝  
██║  ██║███████╗███████║╚██████╔╝██║ ╚████║██║  ██║██║ ╚████║╚██████╗███████╗
╚═╝  ╚═╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
"""


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
    subparsers.add_parser(
        "run", help="Run a scan followed by an audit with automatic fixes"
    )
    subparsers.add_parser(
        "deferred",
        help="Process deferred prompt queue (manual confirmations) without scanning",
    )
    audit_parser = subparsers.add_parser(
        "audit", help="Report directories containing mixed album/artist metadata"
    )
    audit_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically move files whose tags indicate a different artist/album",
    )
    events_parser = subparsers.add_parser(
        "audit-events", help="Show recent pipeline audit events"
    )
    events_parser.add_argument(
        "--limit", type=int, default=50, help="Number of events to show (max 1000)"
    )
    events_parser.add_argument(
        "--event", default=None, help="Filter by event type (e.g. scan_complete)"
    )
    events_parser.add_argument(
        "--since",
        default=None,
        help="Only show events after this id or timestamp (YYYY-MM-DD HH:MM:SS)",
    )
    events_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout",
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
    doctor_parser = subparsers.add_parser(
        "doctor", help="Run basic config/cache/pipeline checks"
    )
    doctor_parser.add_argument(
        "--providers",
        action="store_true",
        help="Also validate providers with network calls",
    )
    export_parser = subparsers.add_parser(
        "export-testcase",
        help="Export a deterministic testcase JSON for release selection/scoring",
    )
    export_parser.add_argument(
        "directory", type=Path, help="Directory (or disc subfolder) to export"
    )
    export_parser.add_argument(
        "--out", type=Path, default=Path("release_selection_case.json")
    )
    export_parser.add_argument(
        "--expected-release",
        default=None,
        help="Optional expected release key (e.g. musicbrainz:<id> or discogs:<id>)",
    )
    args = parser.parse_args()
    config_path = find_config(args.config)
    settings = Settings.load(config_path)
    print(LOGO)
    print("Resonance — music library curator\n")
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
        cache = MetadataCache(settings.daemon.cache_path)
        try:
            cmd_rollback.run(cache)
        finally:
            cache.close()
        return
    requires_providers = args.command in {
        "scan",
        "daemon",
        "run",
        "deferred",
        "export-testcase",
    }
    if requires_providers:
        validate_providers(settings.providers)
    if args.clear_move_cache:
        cache = MetadataCache(settings.daemon.cache_path)
        cache.clear_moves()
        cache.close()
    app: AudioMetaApp | None = None
    daemon: AudioMetaDaemon | None = None
    uses_app = args.command in {
        "scan",
        "daemon",
        "run",
        "deferred",
        "audit",
        "audit-events",
        "singletons",
        "doctor",
        "export-testcase",
    }
    if uses_app:
        app = AudioMetaApp.create(settings)
    if args.command in {"scan", "daemon", "run", "deferred", "export-testcase"} and app:
        daemon = app.get_daemon(
            dry_run_output=args.dry_run_output,
            interactive=(args.command in {"scan", "run", "deferred"}),
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
                cmd_audit_run.run(app.get_auditor(), fix=True)
            case "deferred":
                daemon._process_deferred_directories()
            case "audit":
                cmd_audit_run.run(app.get_auditor(), fix=getattr(args, "fix", False))
            case "audit-events":
                cmd_audit_events.run(
                    app.cache,
                    limit=getattr(args, "limit", 50),
                    event=getattr(args, "event", None),
                    since=getattr(args, "since", None),
                    json_output=getattr(args, "json", False),
                )
            case "cleanup":
                cmd_cleanup.run(settings, dry_run=getattr(args, "dry_run", False))
            case "singletons":
                cmd_singletons.run(app.get_auditor())
            case "doctor":
                report = cmd_doctor.run(
                    settings,
                    validate_providers_online=getattr(args, "providers", False),
                )
                for line in report.checks:
                    print(line)
                if not report.ok:
                    raise SystemExit(1)
            case "export-testcase":
                if not daemon:
                    raise SystemExit("Daemon unavailable")
                cmd_export_testcase.run(
                    daemon,
                    directory=getattr(args, "directory"),
                    out=getattr(args, "out"),
                    expected_release_key=getattr(args, "expected_release", None),
                )
            case _:
                parser.error("Unknown command")
    finally:
        if daemon:
            daemon.report_skips()
        if app:
            app.close()
        if warn_buffer.records:
            print("\n\033[33mWarnings/Errors summary:\033[0m")
            for line in warn_buffer.records:
                print(f" - {line}")
            print(f"\nFull warning log: {warn_log_path}")
