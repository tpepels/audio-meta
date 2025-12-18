import unittest

from audio_meta.daemon_types import ReleaseExample
from audio_meta.release_prompt import (
    ReleasePromptOption,
    append_mb_search_options,
    build_release_prompt_options,
)


class TestReleasePromptBuilder(unittest.TestCase):
    def test_build_release_prompt_options_includes_urls(self) -> None:
        candidates = [("musicbrainz:rid", 1.0)]
        examples = {
            "musicbrainz:rid": ReleaseExample(
                provider="musicbrainz",
                title="Album",
                artist="Artist",
                date="2001",
                track_total=10,
                disc_count=1,
                formats=["CD"],
            )
        }

        def fmt(
            idx: int,
            provider_tag: str,
            artist: str,
            title: str,
            year: str,
            track_count: str,
            disc_label: str,
            format_label: str,
            score,
            release_id: str,
        ) -> str:
            return f"[{provider_tag}] {artist} - {title} ({year}) {track_count} {disc_label} {format_label} {release_id}"

        options = build_release_prompt_options(
            candidates,
            examples,
            split_release_key=lambda key: tuple(key.split(":", 1)),
            parse_year=lambda v: 2001 if v else None,
            disc_label=lambda n: f"{n} disc" if n else None,
            format_option_label=fmt,
            show_urls=True,
        )
        self.assertEqual(len(options), 1)
        self.assertIsInstance(options[0], ReleasePromptOption)
        self.assertIn("https://musicbrainz.org/release/rid", options[0].label)

    def test_append_mb_search_options_dedupes_existing(self) -> None:
        options = [
            ReleasePromptOption(
                idx=1,
                provider="musicbrainz",
                release_id="rid",
                label="x",
                score=1.0,
            )
        ]
        mb_candidates = [
            {"id": "rid", "title": "Album", "artist": "Artist", "score": 0.0},
            {"id": "rid2", "title": "Album2", "artist": "Artist2", "score": 0.5},
            {"id": "rid3", "title": "Album3", "artist": "Artist3", "score": 0.01},
        ]

        def fmt(
            idx: int,
            provider_tag: str,
            artist: str,
            title: str,
            year: str,
            track_count: str,
            disc_label: str,
            format_label: str,
            score,
            release_id: str,
        ) -> str:
            return f"{idx}:{provider_tag}:{release_id}:{score}"

        append_mb_search_options(
            options,
            mb_candidates,
            show_urls=False,
            min_score=0.05,
            parse_year=lambda _v: "?",
            disc_label=lambda _n: None,
            format_option_label=fmt,
        )
        self.assertEqual([o.release_id for o in options], ["rid", "rid2"])


if __name__ == "__main__":
    unittest.main()
