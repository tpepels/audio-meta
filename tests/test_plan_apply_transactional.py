import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from audio_meta.models import ProcessingError, TrackMetadata
from audio_meta.pipeline.contexts import PlanApplyContext
from audio_meta.pipeline.plugins.plan_apply import DefaultPlanApplyPlugin


class TestPlanApplyTransactional(unittest.TestCase):
    def test_rolls_back_move_when_tag_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src_dir = tmp / "src"
            dst_dir = tmp / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()

            src = src_dir / "01.mp3"
            src.write_bytes(b"\x00\x01")
            dest = dst_dir / "01.mp3"

            moves: list[tuple[Path, Path]] = []
            processed: list[Path] = []

            def organizer_move(
                meta: TrackMetadata, target: Path, *, dry_run: bool
            ) -> None:
                assert not dry_run
                target.parent.mkdir(parents=True, exist_ok=True)
                meta.path.rename(target)
                meta.path = target

            class _TagWriter:
                def apply(self, _meta: TrackMetadata) -> None:
                    raise ProcessingError("boom")

            daemon = SimpleNamespace(
                dry_run_recorder=None,
                organizer=SimpleNamespace(
                    enabled=True,
                    move=organizer_move,
                    cleanup_source_directory=lambda _p: None,
                ),
                tag_writer=_TagWriter(),
                cache=SimpleNamespace(
                    record_move=lambda a, b: moves.append((Path(a), Path(b))),
                    set_processed_file=lambda p, *_args, **_kwargs: processed.append(
                        Path(p)
                    ),
                ),
                _safe_stat=lambda p: Path(p).stat(),
            )

            plan = SimpleNamespace(
                meta=TrackMetadata(path=src),
                score=1.0,
                tag_changes={"album": "X"},
                target_path=dest,
            )

            plugin = DefaultPlanApplyPlugin()
            ok = plugin.apply(PlanApplyContext(daemon=daemon, plan=plan))
            self.assertFalse(ok)

            self.assertTrue(src.exists())
            self.assertFalse(dest.exists())
            self.assertEqual(moves, [])
            self.assertEqual(processed, [])


if __name__ == "__main__":
    unittest.main()
