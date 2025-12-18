# Exporting testcases from a remote server

This repo can export deterministic JSON fixtures from a real library (including your cached provider lookups), then replay them locally as unit tests.

## 1) Export on the server

From the server where your music library + `config.yaml` live:

```bash
audio-meta --config /path/to/config.yaml export-testcase "/path/to/Artist/Album" --out /tmp/case.json
```

If the directory is ambiguous and you want a deterministic expected pick, rerun with the release you selected:

```bash
audio-meta --config /path/to/config.yaml export-testcase "/path/to/Artist/Album" --expected-release "musicbrainz:<release-id>" --out /tmp/case.json
```

Suggested real-world outliers to export (high value):
- Chopin Études / similar “same work, multiple performers” releases
- Opera arias / split folders (directory has fewer tracks than the true album)
- Multi-disc classical with offsets (disc+track numbering edge cases)

## 2) Copy the JSON back to this repo

```bash
scp user@server:/tmp/case.json ./tests/fixtures/release_selection/
```

You can add multiple cases; the test harness will run all `*.json` files in that directory.

## 3) Run tests locally

```bash
python -m unittest discover -s tests -p "test_*.py"
```

The fixture replay tests are in `tests/test_exported_release_selection_fixtures.py`.
