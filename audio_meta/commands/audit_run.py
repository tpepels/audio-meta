from __future__ import annotations

from ..audit import LibraryAuditor


def run(auditor: LibraryAuditor, *, fix: bool = False) -> None:
    auditor.run(fix=fix)
