from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .config import Settings, find_config
from .daemon import AudioMetaDaemon
from .providers.validation import validate_providers

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audio metadata correction")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    parser.add_argument(
        "--dry-run-output",
        type=Path,
        help="Record proposed tag changes to this file (JSON Lines) without editing files",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Run a one-off scan")
    subparsers.add_parser("daemon", help="Start the watchdog daemon")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format=LOG_FORMAT)
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    config_path = find_config(args.config)
    settings = Settings.load(config_path)
    validate_providers(settings.providers)
    daemon = AudioMetaDaemon(settings, dry_run_output=args.dry_run_output)

    match args.command:
        case "scan":
            asyncio.run(daemon.run_scan())
        case "daemon":
            asyncio.run(daemon.run_daemon())
        case _:
            parser.error("Unknown command")
