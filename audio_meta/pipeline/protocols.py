from __future__ import annotations

from typing import Any, Optional, Protocol

from .contexts import (
    DirectoryContext,
    PlanApplyContext,
    TrackEnrichmentContext,
    TrackSignalContext,
    TrackSkipContext,
)
from ..daemon_types import PlannedUpdate
from ..release_selection import ReleaseDecision
from .types import ReleaseFinalizeOutcome, TrackSkipDecision, UnmatchedDecision


class ReleaseDecisionPlugin(Protocol):
    name: str

    def decide(self, ctx: DirectoryContext) -> Optional[ReleaseDecision]: ...


class ReleaseFinalizePlugin(Protocol):
    name: str

    def finalize(
        self, ctx: DirectoryContext, decision: ReleaseDecision
    ) -> Optional[ReleaseFinalizeOutcome]: ...


class TrackAssignmentPlugin(Protocol):
    name: str

    def assign(self, ctx: DirectoryContext, force: bool = False) -> bool: ...


class TrackSkipPolicyPlugin(Protocol):
    name: str

    def should_skip(self, ctx: "TrackSkipContext") -> Optional[TrackSkipDecision]: ...


class PlannerPlugin(Protocol):
    name: str

    def build(self, ctx: DirectoryContext) -> Optional[list[PlannedUpdate]]: ...


class PlanTransformPlugin(Protocol):
    name: str

    def transform(self, ctx: DirectoryContext) -> None: ...


class UnmatchedPolicyPlugin(Protocol):
    name: str

    def decide(self, ctx: DirectoryContext) -> Optional[UnmatchedDecision]: ...


class TrackEnricherPlugin(Protocol):
    name: str

    def enrich(self, ctx: TrackEnrichmentContext) -> Optional[Any]: ...


class PlanApplyPlugin(Protocol):
    name: str

    def apply(self, ctx: PlanApplyContext) -> bool: ...


class CandidateSourcePlugin(Protocol):
    name: str

    def add(self, ctx: DirectoryContext) -> None: ...


class SignalExtractorPlugin(Protocol):
    name: str

    def extract(self, ctx: TrackSignalContext) -> None: ...


class DirectoryInitializerPlugin(Protocol):
    name: str

    def initialize(self, ctx: DirectoryContext) -> None: ...


class DirectorySkipPolicyPlugin(Protocol):
    name: str

    def should_skip(self, ctx: DirectoryContext) -> Optional[bool]: ...


class DirectoryAnalyzerPlugin(Protocol):
    name: str

    def analyze(self, ctx: DirectoryContext) -> None: ...


class SingletonHandlerPlugin(Protocol):
    name: str

    def resolve_release_home(self, ctx: DirectoryContext) -> Optional[object]: ...


class DirectoryFinalizePlugin(Protocol):
    name: str

    def finalize(self, ctx: DirectoryContext, applied_plans: bool) -> None: ...


class DirectoryDiagnosticsPlugin(Protocol):
    name: str

    def run(self, ctx: DirectoryContext, applied_plans: bool) -> None: ...


class CacheMaintenancePlugin(Protocol):
    name: str

    def after_scan(self, daemon: object) -> None: ...


class ScanDiagnosticsPlugin(Protocol):
    name: str

    def after_scan(self, daemon: object) -> None: ...
