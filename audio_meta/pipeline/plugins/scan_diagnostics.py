from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..protocols import ScanDiagnosticsPlugin

logger = logging.getLogger(__name__)


class DefaultScanDiagnosticsPlugin(ScanDiagnosticsPlugin):
    name = "default_scan_diagnostics"

    def after_scan(self, daemon: object) -> None:
        cache = getattr(daemon, "cache", None)
        skip_reasons = getattr(daemon, "skip_reasons", None)
        skipped = len(skip_reasons) if isinstance(skip_reasons, dict) else 0
        warning_lines: int | None = None
        warn_log_path: str | None = None
        for handler in logging.getLogger().handlers:
            filename = getattr(handler, "baseFilename", None)
            if not filename:
                continue
            level = getattr(handler, "level", logging.NOTSET)
            if level > logging.WARNING:
                continue
            if not str(filename).endswith("audio-meta-warnings.log"):
                continue
            warn_log_path = str(filename)
            try:
                warning_lines = sum(1 for _ in Path(filename).open("r", encoding="utf-8"))
            except OSError:
                warning_lines = None
            break

        deferred = 0
        if cache is not None:
            try:
                deferred = len(cache.list_deferred_prompts())
            except Exception:
                deferred = 0
        if cache is not None:
            try:
                cache.append_audit_event(
                    "scan_complete",
                    {
                        "skipped_directories": skipped,
                        "deferred_prompts": deferred,
                        "warning_lines": warning_lines,
                        "warning_log_path": warn_log_path,
                    },
                )
            except Exception:  # pragma: no cover
                logger.exception("Failed to write scan audit event")
        if warning_lines is None:
            logger.info("Scan complete: skipped=%d deferred=%d", skipped, deferred)
        else:
            logger.info("Scan complete: skipped=%d deferred=%d warnings=%d", skipped, deferred, warning_lines)
