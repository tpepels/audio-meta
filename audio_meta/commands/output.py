from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class CheckLine:
    label: str
    status: str
    detail: Optional[str] = None

    def render(self) -> str:
        if self.detail:
            return f"{self.label}: {self.status} ({self.detail})"
        return f"{self.label}: {self.status}"


def ok(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "OK", detail).render()


def warning(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "WARNING", detail).render()


def error(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "ERROR", detail).render()


def skipped(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "SKIPPED", detail).render()


def enabled(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "ENABLED", detail).render()


def disabled(label: str, detail: Optional[str] = None) -> str:
    return CheckLine(label, "DISABLED", detail).render()

