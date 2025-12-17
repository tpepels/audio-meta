from __future__ import annotations

import logging
from importlib import metadata
from typing import Any, Iterable, Optional

from .contexts import DirectoryContext, PlanApplyContext, TrackEnrichmentContext, TrackSignalContext, TrackSkipContext
from .protocols import (
    CandidateSourcePlugin,
    CacheMaintenancePlugin,
    DirectoryAnalyzerPlugin,
    DirectoryDiagnosticsPlugin,
    DirectoryFinalizePlugin,
    DirectoryInitializerPlugin,
    DirectorySkipPolicyPlugin,
    ReleaseFinalizePlugin,
    PlanApplyPlugin,
    ReleaseDecisionPlugin,
    PlannerPlugin,
    PlanTransformPlugin,
    ScanDiagnosticsPlugin,
    SignalExtractorPlugin,
    SingletonHandlerPlugin,
    TrackSkipPolicyPlugin,
    TrackAssignmentPlugin,
    TrackEnricherPlugin,
    UnmatchedPolicyPlugin,
)
from .types import ReleaseFinalizeOutcome, UnmatchedDecision
from .plugins.candidates_discogs import DiscogsCandidateSourcePlugin
from .plugins.candidates_musicbrainz import MusicBrainzCandidateSourcePlugin
from .plugins.directory_analyzer import DefaultDirectoryAnalyzerPlugin
from .plugins.directory_initializer import DefaultDirectoryInitializerPlugin
from .plugins.directory_skip import DefaultDirectorySkipPolicyPlugin
from .plugins.plan_apply import DefaultPlanApplyPlugin
from .plugins.no_candidate_manual_selection import NoCandidateManualSelectionPlugin
from .plugins.release_decision import DefaultReleaseDecisionPlugin
from .plugins.release_finalize import DefaultReleaseFinalizePlugin
from .plugins.planner import DefaultPlannerPlugin
from .plugins.directory_finalize import DefaultDirectoryFinalizePlugin
from .plugins.directory_diagnostics import DefaultDirectoryDiagnosticsPlugin
from .plugins.cache_maintenance import DefaultCacheMaintenancePlugin
from .plugins.scan_diagnostics import DefaultScanDiagnosticsPlugin
from .plugins.plan_transform_singleton_target import SingletonTargetOverridePlugin
from .plugins.directory_skip_processed import ProcessedDirectorySkipPolicyPlugin
from .plugins.directory_audit import DirectoryAuditPlugin
from .plugins.singleton_handler import DefaultSingletonHandlerPlugin
from .plugins.track_skip import DefaultTrackSkipPolicyPlugin
from .plugins.signal_extractor import DefaultSignalExtractorPlugin
from .plugins.track_assignment import DefaultTrackAssignmentPlugin
from .plugins.track_enricher import DefaultTrackEnricherPlugin
from .plugins.unmatched_policy import DefaultUnmatchedPolicyPlugin
from ..release_selection import ReleaseDecision
from ..daemon_types import PlannedUpdate

logger = logging.getLogger(__name__)

DEFAULT_PIPELINE_ORDER: dict[str, list[str]] = {
    # Skip expensive work first (safe, deterministic checks).
    "directory_skip_policies": [
        "processed_directory_skip",
        "default_directory_skip",
    ],
    # Prefer MusicBrainz for canonical metadata; Discogs supplements/backs off.
    "candidate_sources": [
        "musicbrainz_candidate_source",
        "discogs_candidate_source",
    ],
    # Diagnostics first, then persist audit events.
    "directory_diagnostics": [
        "default_directory_diagnostics",
        "directory_audit",
    ],
    # Ensure singleton target override runs after planning.
    "plan_transforms": [
        "singleton_target_override",
    ],
}


def _select_entry_points(group: str) -> Iterable[Any]:
    try:
        eps = metadata.entry_points()
        if hasattr(eps, "select"):
            return eps.select(group=group)  # type: ignore[attr-defined]
        return eps.get(group, [])  # type: ignore[return-value]
    except Exception:  # pragma: no cover - depends on runtime packaging
        return []


def _load_plugins(group: str) -> list[Any]:
    plugins: list[Any] = []
    for ep in _select_entry_points(group):
        try:
            loaded = ep.load()
            plugin = loaded() if callable(loaded) else loaded
            if plugin is not None:
                plugins.append(plugin)
        except Exception as exc:  # pragma: no cover - plugin errors
            logger.warning("Failed to load plugin %s from %s: %s", getattr(ep, "name", ep), group, exc)
    return plugins


class ProcessingPipeline:
    """
    Minimal plugin-style pipeline.

    External plugins may be registered via Python entry points. Each group should
    provide a callable (or instance) implementing the appropriate protocol:

      - `audio_meta.release_deciders`
      - `audio_meta.release_appliers`
      - `audio_meta.track_assigners`
      - `audio_meta.unmatched_policies`
      - `audio_meta.track_enrichers`
      - `audio_meta.plan_appliers`
      - `audio_meta.candidate_sources`
      - `audio_meta.signal_extractors`
      - `audio_meta.directory_initializers`
      - `audio_meta.directory_skip_policies`
      - `audio_meta.directory_analyzers`
      - `audio_meta.track_skip_policies`
      - `audio_meta.planners`
      - `audio_meta.singleton_handlers`
      - `audio_meta.directory_finalizers`
      - `audio_meta.directory_diagnostics`
      - `audio_meta.cache_maintainers`
      - `audio_meta.scan_diagnostics`
      - `audio_meta.plan_transforms`
    """

    def __init__(
        self,
        *,
        disabled_plugins: Optional[set[str]] = None,
        plugin_order: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self._disabled_plugins = {name.strip() for name in (disabled_plugins or set()) if name and name.strip()}
        configured = {k: list(v) for k, v in (plugin_order or {}).items()}
        self._plugin_order = {k: list(v) for k, v in DEFAULT_PIPELINE_ORDER.items()}
        for key, order in configured.items():
            self._plugin_order[key] = order
        self._release_deciders = list(_load_plugins("audio_meta.release_deciders"))
        self._track_assigners = list(_load_plugins("audio_meta.track_assigners"))
        self._unmatched_policies = list(_load_plugins("audio_meta.unmatched_policies"))
        self._track_enrichers = list(_load_plugins("audio_meta.track_enrichers"))
        self._plan_appliers = list(_load_plugins("audio_meta.plan_appliers"))
        self._candidate_sources = list(_load_plugins("audio_meta.candidate_sources"))
        self._signal_extractors = list(_load_plugins("audio_meta.signal_extractors"))
        self._directory_initializers = list(_load_plugins("audio_meta.directory_initializers"))
        self._directory_skip_policies = list(_load_plugins("audio_meta.directory_skip_policies"))
        self._directory_analyzers = list(_load_plugins("audio_meta.directory_analyzers"))
        self._release_finalizers: list[ReleaseFinalizePlugin] = list(_load_plugins("audio_meta.release_appliers"))
        self._track_skip_policies: list[TrackSkipPolicyPlugin] = list(_load_plugins("audio_meta.track_skip_policies"))
        self._planners: list[PlannerPlugin] = list(_load_plugins("audio_meta.planners"))
        self._singleton_handlers: list[SingletonHandlerPlugin] = list(_load_plugins("audio_meta.singleton_handlers"))
        self._directory_finalizers: list[DirectoryFinalizePlugin] = list(_load_plugins("audio_meta.directory_finalizers"))
        self._directory_diagnostics: list[DirectoryDiagnosticsPlugin] = list(_load_plugins("audio_meta.directory_diagnostics"))
        self._cache_maintainers: list[CacheMaintenancePlugin] = list(_load_plugins("audio_meta.cache_maintainers"))
        self._scan_diagnostics: list[ScanDiagnosticsPlugin] = list(_load_plugins("audio_meta.scan_diagnostics"))
        self._plan_transforms: list[PlanTransformPlugin] = list(_load_plugins("audio_meta.plan_transforms"))

        self._release_deciders = [p for p in self._release_deciders if getattr(p, "name", "") not in self._disabled_plugins]
        self._track_assigners = [p for p in self._track_assigners if getattr(p, "name", "") not in self._disabled_plugins]
        self._unmatched_policies = [p for p in self._unmatched_policies if getattr(p, "name", "") not in self._disabled_plugins]
        self._track_enrichers = [p for p in self._track_enrichers if getattr(p, "name", "") not in self._disabled_plugins]
        self._plan_appliers = [p for p in self._plan_appliers if getattr(p, "name", "") not in self._disabled_plugins]
        self._candidate_sources = [p for p in self._candidate_sources if getattr(p, "name", "") not in self._disabled_plugins]
        self._signal_extractors = [p for p in self._signal_extractors if getattr(p, "name", "") not in self._disabled_plugins]
        self._directory_initializers = [p for p in self._directory_initializers if getattr(p, "name", "") not in self._disabled_plugins]
        self._directory_skip_policies = [p for p in self._directory_skip_policies if getattr(p, "name", "") not in self._disabled_plugins]
        self._directory_analyzers = [p for p in self._directory_analyzers if getattr(p, "name", "") not in self._disabled_plugins]
        self._track_skip_policies = [p for p in self._track_skip_policies if getattr(p, "name", "") not in self._disabled_plugins]
        self._planners = [p for p in self._planners if getattr(p, "name", "") not in self._disabled_plugins]
        self._singleton_handlers = [p for p in self._singleton_handlers if getattr(p, "name", "") not in self._disabled_plugins]
        self._directory_finalizers = [p for p in self._directory_finalizers if getattr(p, "name", "") not in self._disabled_plugins]
        self._directory_diagnostics = [p for p in self._directory_diagnostics if getattr(p, "name", "") not in self._disabled_plugins]
        self._cache_maintainers = [p for p in self._cache_maintainers if getattr(p, "name", "") not in self._disabled_plugins]
        self._scan_diagnostics = [p for p in self._scan_diagnostics if getattr(p, "name", "") not in self._disabled_plugins]
        self._plan_transforms = [p for p in self._plan_transforms if getattr(p, "name", "") not in self._disabled_plugins]

        def _append(plugin_list: list[Any], plugin: Any) -> None:
            name = getattr(plugin, "name", "") or plugin.__class__.__name__
            if name in self._disabled_plugins:
                return
            plugin_list.append(plugin)

        _append(self._release_deciders, NoCandidateManualSelectionPlugin())
        _append(self._release_deciders, DefaultReleaseDecisionPlugin())
        _append(self._release_finalizers, DefaultReleaseFinalizePlugin())
        _append(self._track_assigners, DefaultTrackAssignmentPlugin())
        _append(self._unmatched_policies, DefaultUnmatchedPolicyPlugin())
        _append(self._track_enrichers, DefaultTrackEnricherPlugin())
        _append(self._plan_appliers, DefaultPlanApplyPlugin())
        _append(self._candidate_sources, MusicBrainzCandidateSourcePlugin())
        _append(self._candidate_sources, DiscogsCandidateSourcePlugin())
        _append(self._signal_extractors, DefaultSignalExtractorPlugin())
        _append(self._directory_initializers, DefaultDirectoryInitializerPlugin())
        _append(self._directory_skip_policies, ProcessedDirectorySkipPolicyPlugin())
        _append(self._directory_skip_policies, DefaultDirectorySkipPolicyPlugin())
        _append(self._directory_analyzers, DefaultDirectoryAnalyzerPlugin())
        _append(self._track_skip_policies, DefaultTrackSkipPolicyPlugin())
        _append(self._planners, DefaultPlannerPlugin())
        _append(self._singleton_handlers, DefaultSingletonHandlerPlugin())
        _append(self._directory_finalizers, DefaultDirectoryFinalizePlugin())
        _append(self._directory_diagnostics, DefaultDirectoryDiagnosticsPlugin())
        _append(self._directory_diagnostics, DirectoryAuditPlugin())
        _append(self._cache_maintainers, DefaultCacheMaintenancePlugin())
        _append(self._scan_diagnostics, DefaultScanDiagnosticsPlugin())
        _append(self._plan_transforms, SingletonTargetOverridePlugin())

        self._apply_plugin_order()

    def _apply_plugin_order(self) -> None:
        def reorder(plugins: list[Any], order: list[str]) -> list[Any]:
            if not order:
                return plugins
            named = [(getattr(p, "name", ""), p) for p in plugins]
            first: list[Any] = []
            used: set[int] = set()
            for name in order:
                for idx, (pname, plugin) in enumerate(named):
                    if idx in used:
                        continue
                    if pname == name:
                        first.append(plugin)
                        used.add(idx)
                        break
            rest = [plugin for idx, (_, plugin) in enumerate(named) if idx not in used]
            return first + rest

        mapping: dict[str, str] = {
            "release_deciders": "_release_deciders",
            "release_appliers": "_release_finalizers",
            "track_assigners": "_track_assigners",
            "unmatched_policies": "_unmatched_policies",
            "track_enrichers": "_track_enrichers",
            "plan_appliers": "_plan_appliers",
            "candidate_sources": "_candidate_sources",
            "signal_extractors": "_signal_extractors",
            "directory_initializers": "_directory_initializers",
            "directory_skip_policies": "_directory_skip_policies",
            "directory_analyzers": "_directory_analyzers",
            "track_skip_policies": "_track_skip_policies",
            "planners": "_planners",
            "plan_transforms": "_plan_transforms",
            "singleton_handlers": "_singleton_handlers",
            "directory_finalizers": "_directory_finalizers",
            "directory_diagnostics": "_directory_diagnostics",
            "cache_maintainers": "_cache_maintainers",
            "scan_diagnostics": "_scan_diagnostics",
        }
        for key, attr in mapping.items():
            order = self._plugin_order.get(key) or []
            plugins = getattr(self, attr, None)
            if isinstance(plugins, list):
                setattr(self, attr, reorder(plugins, order))
        # Note: plan transforms are appended during initialization; do not append here.

    def analyze_directory(self, ctx: DirectoryContext) -> None:
        for plugin in self._directory_analyzers:
            try:
                plugin.analyze(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Directory-analyzer plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def decide_release(self, ctx: DirectoryContext) -> ReleaseDecision:
        last_exc: Exception | None = None
        for plugin in self._release_deciders:
            try:
                result = plugin.decide(ctx)
            except Exception as exc:  # pragma: no cover - plugin errors
                last_exc = exc
                logger.exception("Release-decider plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if result is not None:
                return result
        if last_exc:
            raise last_exc
        return DefaultReleaseDecisionPlugin().decide(ctx)  # type: ignore[return-value]

    def finalize_release(self, ctx: DirectoryContext, decision: ReleaseDecision) -> ReleaseFinalizeOutcome:
        last_exc: Exception | None = None
        for plugin in self._release_finalizers:
            try:
                result = plugin.finalize(ctx, decision)
            except Exception as exc:  # pragma: no cover - plugin errors
                last_exc = exc
                logger.exception("Release-finalize plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if result is not None:
                return result
        if last_exc:
            raise last_exc
        result = DefaultReleaseFinalizePlugin().finalize(ctx, decision)
        if result is None:  # pragma: no cover - default should always handle
            return ReleaseFinalizeOutcome(
                provider=None,
                release_id=None,
                album_name="",
                album_artist="",
                discogs_release_details=None,
                release_summary_printed=decision.release_summary_printed,
            )
        return result

    def assign_tracks(self, ctx: DirectoryContext, force: bool = False) -> bool:
        last_exc: Exception | None = None
        for plugin in self._track_assigners:
            try:
                handled = plugin.assign(ctx, force=force)
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                logger.exception("Track-assigner plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if handled:
                return True
        if last_exc:
            raise last_exc
        return False

    def handle_unmatched(self, ctx: DirectoryContext) -> UnmatchedDecision:
        last_exc: Exception | None = None
        for plugin in self._unmatched_policies:
            try:
                decision = plugin.decide(ctx)
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                logger.exception(
                    "Unmatched-policy plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
                continue
            if decision is not None:
                return decision
        if last_exc:
            raise last_exc
        return DefaultUnmatchedPolicyPlugin().decide(ctx)  # type: ignore[return-value]

    def enrich_track(self, ctx: TrackEnrichmentContext) -> Optional[object]:
        last_exc: Exception | None = None
        for plugin in self._track_enrichers:
            try:
                result = plugin.enrich(ctx)
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                logger.exception("Track-enricher plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if result is not None:
                return result
        if last_exc:
            raise last_exc
        return None

    def should_skip_track(self, ctx: "TrackSkipContext") -> bool:
        for plugin in self._track_skip_policies:
            try:
                decision = plugin.should_skip(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Track-skip plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
                continue
            if decision is not None:
                if decision.should_skip and decision.reason and getattr(ctx, "directory_ctx", None) is not None:
                    dir_ctx = ctx.directory_ctx
                    try:
                        dir_ctx.diagnostics.setdefault("skipped_tracks", []).append(
                            {"path": str(ctx.file_path), "reason": decision.reason}
                        )
                    except Exception:
                        pass
                return bool(decision.should_skip)
        return False

    def build_plans(self, ctx: DirectoryContext) -> list[PlannedUpdate]:
        last_exc: Exception | None = None
        for plugin in self._planners:
            try:
                result = plugin.build(ctx)
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                logger.exception("Planner plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if result is not None:
                return list(result)
        if last_exc:
            raise last_exc
        return list(ctx.planned)

    def transform_plans(self, ctx: DirectoryContext) -> None:
        for plugin in self._plan_transforms:
            try:
                plugin.transform(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Plan-transform plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def resolve_singleton_release_home(self, ctx: DirectoryContext) -> Optional[object]:
        for plugin in self._singleton_handlers:
            try:
                result = plugin.resolve_release_home(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Singleton-handler plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
                continue
            if result is not None:
                return result
        return None

    def finalize_directory(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        for plugin in self._directory_finalizers:
            try:
                plugin.finalize(ctx, applied_plans=applied_plans)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Directory-finalizer plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def complete_directory(self, ctx: DirectoryContext, applied_plans: bool) -> None:
        for plugin in self._directory_diagnostics:
            try:
                plugin.run(ctx, applied_plans=applied_plans)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Directory-diagnostics plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
        self.finalize_directory(ctx, applied_plans=applied_plans)

    def after_scan(self, daemon: object) -> None:
        for plugin in self._cache_maintainers:
            try:
                plugin.after_scan(daemon)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Cache-maintenance plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
        for plugin in self._scan_diagnostics:
            try:
                plugin.after_scan(daemon)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Scan-diagnostics plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def apply_plan(self, ctx: PlanApplyContext) -> bool:
        last_exc: Exception | None = None
        for plugin in self._plan_appliers:
            try:
                handled = plugin.apply(ctx)
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                logger.exception("Plan-applier plugin %s failed", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if handled:
                return True
        if last_exc:
            raise last_exc
        return False

    def add_candidates(self, ctx: DirectoryContext) -> None:
        for plugin in self._candidate_sources:
            try:
                plugin.add(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Candidate-source plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def extract_signals(self, ctx: TrackSignalContext) -> None:
        for plugin in self._signal_extractors:
            try:
                plugin.extract(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Signal-extractor plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def initialize_directory(self, ctx: DirectoryContext) -> None:
        for plugin in self._directory_initializers:
            try:
                plugin.initialize(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Directory-initializer plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )

    def should_skip_directory(self, ctx: DirectoryContext) -> bool:
        for plugin in self._directory_skip_policies:
            try:
                decision = plugin.should_skip(ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "Directory-skip plugin %s failed",
                    getattr(plugin, "name", plugin.__class__.__name__),
                )
                continue
            if decision is not None:
                return bool(decision)
        return False
