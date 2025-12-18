import unittest
from pathlib import Path

from audio_meta.daemon_types import ReleaseExample
from audio_meta.release_selection import _auto_pick_best_fit_release, decide_release


class _FakeDaemon:
    def __init__(self) -> None:
        self.interactive = False
        self.defer_prompts = False
        self._processing_deferred = False
        self.discogs = None

        self.recorded_skips: list[tuple[Path, str]] = []
        self.warned_ambiguous: list[Path] = []

        self._equivalent_pick: str | None = None
        self._home_pick: str | None = None
        self._coverage_map: dict[str | None, float] = {}
        self._release_home_counts: dict[str, int] = {}

    @staticmethod
    def _release_key(provider: str, release_id: str) -> str:
        return f"{provider}:{release_id}"

    @staticmethod
    def _split_release_key(key: str) -> tuple[str, str | None]:
        if ":" in key:
            provider, rid = key.split(":", 1)
            return provider, rid
        return "musicbrainz", key

    @staticmethod
    def _display_path(path: Path) -> str:
        return str(path)

    def _record_skip(self, directory: Path, reason: str) -> None:
        self.recorded_skips.append((directory, reason))

    def _warn_ambiguous_release(self, directory: Path, *_args, **_kwargs) -> None:
        self.warned_ambiguous.append(directory)

    def _schedule_deferred_directory(self, _directory: Path, _reason: str) -> None:
        raise AssertionError("defer_prompts should be disabled in these tests")

    def _auto_pick_equivalent_release(self, _candidates, *_args, **_kwargs) -> str | None:
        return self._equivalent_pick

    def _auto_pick_existing_release_home(self, _candidates, *_args, **_kwargs) -> str | None:
        return self._home_pick

    def _release_home_for_key(self, release_key: str, _current_dir: Path, _current_count: int):
        return None, int(self._release_home_counts.get(release_key, 0))

    def _adjust_release_scores(self, scores, _examples, *_args, **_kwargs):
        return scores, dict(self._coverage_map)


class TestReleaseSelectionEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.daemon = _FakeDaemon()
        self.directory = Path("/music/Some Artist/Some Album")

    def _example(self, provider: str, title: str, artist: str, track_total: int | None) -> ReleaseExample:
        return ReleaseExample(
            provider=provider,
            title=title,
            artist=artist,
            date="2000",
            track_total=track_total,
            disc_count=1,
            formats=[],
        )

    def test_empty_scores_returns_no_selection(self) -> None:
        decision = decide_release(
            self.daemon,
            self.directory,
            file_count=10,
            is_singleton=False,
            dir_track_count=10,
            dir_year=2000,
            pending_results=[],
            release_scores={},
            release_examples={},
            discogs_details={},
            forced_provider=None,
            forced_release_id=None,
            forced_release_score=0.0,
            force_prompt=False,
            release_summary_printed=False,
        )
        self.assertIsNone(decision.best_release_id)
        self.assertFalse(decision.should_abort)

    def test_forced_release_overrides_ambiguity(self) -> None:
        scores = {"musicbrainz:mb1": 0.9, "musicbrainz:mb2": 0.89}
        decision = decide_release(
            self.daemon,
            self.directory,
            file_count=10,
            is_singleton=False,
            dir_track_count=10,
            dir_year=2000,
            pending_results=[],
            release_scores=dict(scores),
            release_examples={},
            discogs_details={},
            forced_provider="musicbrainz",
            forced_release_id="mb2",
            forced_release_score=1.0,
            force_prompt=False,
            release_summary_printed=False,
        )
        self.assertEqual(decision.best_release_id, "musicbrainz:mb2")
        self.assertEqual(decision.ambiguous_candidates, [("musicbrainz:mb2", decision.best_score)])
        self.assertFalse(decision.should_abort)

    def test_auto_pick_equivalent_release_collapses_candidates(self) -> None:
        self.daemon._equivalent_pick = "musicbrainz:mb2"
        scores = {"musicbrainz:mb1": 0.9, "musicbrainz:mb2": 0.88}
        decision = decide_release(
            self.daemon,
            self.directory,
            file_count=10,
            is_singleton=False,
            dir_track_count=10,
            dir_year=2000,
            pending_results=[],
            release_scores=dict(scores),
            release_examples={
                "musicbrainz:mb1": self._example("musicbrainz", "A", "Artist", 10),
                "musicbrainz:mb2": self._example("musicbrainz", "A (deluxe)", "Artist", 10),
            },
            discogs_details={},
            forced_provider=None,
            forced_release_id=None,
            forced_release_score=0.0,
            force_prompt=False,
            release_summary_printed=False,
        )
        self.assertEqual(decision.best_release_id, "musicbrainz:mb2")
        self.assertEqual(decision.ambiguous_candidates, [("musicbrainz:mb2", decision.best_score)])
        self.assertFalse(decision.should_abort)

    def test_singleton_home_pick_wins_when_ambiguous(self) -> None:
        self.daemon._home_pick = "musicbrainz:mb2"
        scores = {"musicbrainz:mb1": 0.9, "musicbrainz:mb2": 0.89}
        decision = decide_release(
            self.daemon,
            self.directory,
            file_count=1,
            is_singleton=True,
            dir_track_count=1,
            dir_year=2000,
            pending_results=[],
            release_scores=dict(scores),
            release_examples={
                "musicbrainz:mb1": self._example("musicbrainz", "A", "Artist", 1),
                "musicbrainz:mb2": self._example("musicbrainz", "B", "Artist", 1),
            },
            discogs_details={},
            forced_provider=None,
            forced_release_id=None,
            forced_release_score=0.0,
            force_prompt=False,
            release_summary_printed=False,
        )
        self.assertEqual(decision.best_release_id, "musicbrainz:mb2")
        self.assertEqual(decision.ambiguous_candidates, [("musicbrainz:mb2", decision.best_score)])
        self.assertFalse(decision.should_abort)

    def test_low_coverage_noninteractive_aborts(self) -> None:
        self.daemon._coverage_map = {"musicbrainz:mb1": 0.4}
        with self.assertLogs("audio_meta.release_selection", level="WARNING"):
            decision = decide_release(
                self.daemon,
                self.directory,
                file_count=10,
                is_singleton=False,
                dir_track_count=10,
                dir_year=2000,
                pending_results=[],
                release_scores={"musicbrainz:mb1": 0.9},
                release_examples={"musicbrainz:mb1": self._example("musicbrainz", "A", "Artist", 10)},
                discogs_details={},
                forced_provider=None,
                forced_release_id=None,
                forced_release_score=0.0,
                force_prompt=False,
                release_summary_printed=False,
            )
        self.assertTrue(decision.should_abort)
        self.assertTrue(any("Low coverage" in reason for _, reason in self.daemon.recorded_skips))


class TestAutoPickBestFitRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.daemon = _FakeDaemon()
        self.directory = Path("/music/Some Artist/Some Album")

    def _example(self, track_total: int | None) -> ReleaseExample:
        return ReleaseExample(
            provider="musicbrainz",
            title="Album",
            artist="Artist",
            date="2000",
            track_total=track_total,
            disc_count=1,
            formats=[],
        )

    def test_picks_best_track_total_fit(self) -> None:
        examples = {
            "musicbrainz:mb1": self._example(10),
            "musicbrainz:mb2": self._example(20),
        }
        choice = _auto_pick_best_fit_release(
            self.daemon,
            candidates=[("musicbrainz:mb1", 0.8), ("musicbrainz:mb2", 0.8)],
            directory=self.directory,
            file_count=10,
            dir_track_count=10,
            release_examples=examples,
        )
        self.assertEqual(choice, "musicbrainz:mb1")

    def test_no_pick_when_fit_below_threshold(self) -> None:
        examples = {
            "musicbrainz:mb1": self._example(8),
            "musicbrainz:mb2": self._example(9),
        }
        choice = _auto_pick_best_fit_release(
            self.daemon,
            candidates=[("musicbrainz:mb1", 0.9), ("musicbrainz:mb2", 0.9)],
            directory=self.directory,
            file_count=10,
            dir_track_count=10,
            release_examples=examples,
        )
        self.assertIsNone(choice)

    def test_home_count_can_improve_fit(self) -> None:
        self.daemon._release_home_counts["musicbrainz:mb1"] = 10
        examples = {
            "musicbrainz:mb1": self._example(3),
            "musicbrainz:mb2": self._example(9),
        }
        choice = _auto_pick_best_fit_release(
            self.daemon,
            candidates=[("musicbrainz:mb1", 0.8), ("musicbrainz:mb2", 0.8)],
            directory=self.directory,
            file_count=10,
            dir_track_count=10,
            release_examples=examples,
        )
        self.assertEqual(choice, "musicbrainz:mb1")


if __name__ == "__main__":
    unittest.main()
