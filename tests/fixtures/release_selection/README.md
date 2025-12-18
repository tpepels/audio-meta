# Exported release-selection fixtures

These fixtures are generated with the `audio-meta export-testcase` command and are
used by `tests/test_exported_release_selection_fixtures.py`.

## Adding real-world outliers (recommended)

Run these on the machine that has the music files available:

```sh
audio-meta --config config.yaml export-testcase \
  --directory "/path/to/Your/Album" \
  --out tests/fixtures/release_selection/your_case_name.json
```

If you want to lock a specific expected album choice (for ambiguous prompts), pass
`--expected-release musicbrainz:<release-id>` or `discogs:<release-id>` depending
on what you’re testing.

Suggested real-world outliers to add (from current issues):
- Chopin Études / similar “same work, multiple performers” releases
- Opera arias / split folders (track-count mismatch, partial albums)
- Multi-disc classical with offsets (disc/track numbering edge cases)
