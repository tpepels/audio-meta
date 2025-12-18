# audio-meta

`audio-meta` is a command-line tool that keeps large audio libraries tidy. It fingerprints every track, fetches canonical metadata from MusicBrainz (with Discogs fallbacks), rewrites the tags, and—if you want—moves the files into a Plex-friendly layout such as `/Artist/Album` or `/Composer/Performer/Album`. The default workflow is designed for unattended operation: ambiguous releases are deferred until the scan finishes, and an audit pass can re-check the library using the tags already on disk.

## Highlights

- **Accurate matching** – Chromaprint fingerprints + release-level caching make repeat scans fast while still catching new files.
- **Tag-aware heuristics** – Existing ID3/FLAC/M4A tags are taken into account to avoid misclassification and to bias scoring toward the right release.
- **Organizer** – Optional mover keeps your library structured for Plex, automatically cleaning empty directories afterwards.
- **Audit & repair** – Reads the tags already on disk to detect (and optionally fix) files that live in the wrong artist/album folder.
- **Deferred prompts** – When a manual decision is needed, the question is queued and presented after the scan completes (the queue is persisted so you can answer later).
- **Daemon mode** – Integrates with `systemd` via the provided unit file.

## Requirements

- Python 3.10 or newer (tested on Debian/Ubuntu).
- `python3-venv`, `libchromaprint-tools`, `ffmpeg`/`libavcodec` for fingerprinting and decoding.
- API credentials:
  - [AcoustID API key](https://acoustid.org/api-key) (mandatory).
  - [Discogs token](https://www.discogs.com/settings/developers) (optional but recommended for obscure releases).

## Installation

```bash
git clone https://github.com/your-user/audio-meta.git
cd audio-meta
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp config.sample.yaml config.yaml
```

Edit `config.yaml` and add your library paths plus API keys:

```yaml
library:
  roots:
    - /srv/music
providers:
  acoustid_api_key: "YOUR-ACOUSTID-KEY"
  musicbrainz_useragent: "audio-meta/0.1 (you@example.com)"
  discogs_token: "YOUR-DISCOGS-TOKEN"
organizer:
  enabled: true
  target_root: /srv/music
  cleanup_empty_dirs: true
```

## Everyday workflow

1. **First run (step-by-step)**
   1. Validate config and cache setup (optional): `audio-meta --config config.yaml doctor`
   2. Run a scan: `audio-meta --config config.yaml scan`
   3. Answer any deferred release prompts after the scan finishes (they persist if you quit early).
   4. Run the audit and auto-fix stragglers: `audio-meta --config config.yaml audit --fix`
   5. Review single-file directories: `audio-meta --config config.yaml singletons`

2. **Daily use**
   - `audio-meta --config config.yaml run` runs a scan (tagging + organizer moves) and then runs the audit with `--fix`.

3. **Review the summary**
   - Warnings (including files that could not be matched or moved) are printed at the end and written to `audio-meta-warnings.log` in the working directory.

### Other commands

| Command | Description |
| ------- | ----------- |
| `audio-meta --config config.yaml scan` | Run only the scanner (no audit). Use when you want to inspect before fixing. |
| `audio-meta --config config.yaml audit` | Report misplaced files based on tags; add `--fix` to auto-move them. |
| `audio-meta --config config.yaml audit-events` | Show recent pipeline audit events stored in the cache. |
| `audio-meta --config config.yaml cleanup [--dry-run]` | Remove directories that contain no audio files (e.g., leftover artwork). |
| `audio-meta --config config.yaml singletons` | Interactively review directories that currently contain a single audio file (keep/move/delete/ignore). |
| `audio-meta --config config.yaml rollback-moves` | Undo the most recent organizer moves using the move history stored in the cache. |
| `audio-meta --config config.yaml daemon` | Start the filesystem watcher to process new files continuously. |
| `audio-meta --config config.yaml doctor` | Validate config/cache/pipeline; pass `--providers` to validate provider credentials via network. |
| `audio-meta --config config.yaml export-testcase DIR --out case.json` | Export a deterministic release-selection fixture for unit tests (see `docs/TESTCASES.md`). |

Useful global flags (place them before the subcommand):

- `--config config.yaml` – select the configuration file (required).
- `--log-level WARN|INFO|DEBUG` – control verbosity (defaults to INFO).
- `--dry-run-output path.jsonl` – emit JSON lines describing planned changes without touching files.

Advanced / troubleshooting flags:

- `--disable-release-cache` – ignore cached directory-release matches for this run.
- `--reset-release-cache` – drop all stored directory-release matches before starting.

### Pipeline tuning

The scanner is built from a plugin pipeline. You can disable specific plugins or override plugin order per stage:

- `daemon.pipeline_disable` – list of plugin names to skip (e.g. `track_assignment`).
- `daemon.pipeline_order` – per-stage list of plugin names; unspecified plugins run after the explicitly listed ones.

See `config.sample.yaml` for the available stages and default ordering.
For a stage-by-stage description of where decisions happen, see `docs/PIPELINE.md`.

## Architecture boundaries

- **Runtime**: `audio_meta/cli.py` (arg parsing), `audio_meta/app.py` (wires shared dependencies), `audio_meta/daemon/core.py` (async workers + watchdog).
- **Business logic**: pipeline stages in `audio_meta/pipeline/plugins/`, plus reusable modules like `audio_meta/release_selection.py`, `audio_meta/release_home.py`, `audio_meta/release_scoring.py`, `audio_meta/organizer.py`, `audio_meta/tagging.py`.
- **State**: `audio_meta/cache.py` is the persistent store used by both scan and audit flows.

## Deferred prompts & manual selections

- When the scanner cannot confidently choose a release (or when coverage is low), the directory is added to a deferred queue along with the reason.
- Some safety checks also defer directories:
  - **Suspicious fingerprint**: AcoustID match contradicts existing tags (album/album artist).
  - **Classical credits**: performer credits are missing or inconsistent across tracks.
- After the scan, the tool replays that queue and shows a menu that lists MusicBrainz / Discogs candidates, including track counts, formats, and scores.
- Input is numeric (e.g., `1`) for the best candidate, or `mb:<release-id>` / `dg:<release-id>` if you have a specific release in mind.
- Additional options:
  - `0` – skip this directory (it will be listed in the warning summary).
  - `d` – delete the directory.
  - `a` – archive the directory (requires `organizer.archive_root`).
  - `i` – ignore the directory in future scans.

## Reviewing single-file directories

The audit stage highlights directories that contain only one audio file (often artwork folders or stray tracks). You can process them interactively at any time:

```
audio-meta --config config.yaml singletons
```

For each entry you can press **Enter** to keep it, choose **m** to move it under the suggested artist/album, choose **d** to delete it, or choose **i** to ignore it permanently (the ignore flag is stored in the metadata cache so future runs skip it). This review uses the same cache as the scanner, so previous decisions are remembered.

## Running as a daemon

1. Copy `systemd/audio-meta.service` to `/etc/systemd/system/audio-meta.service`.
2. Edit the service file so it points to your virtual environment and configuration file.
3. Reload systemd and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now audio-meta.service
   ```
4. View logs with `journalctl -u audio-meta.service`.

The daemon mode is useful when new files arrive regularly; it still uses deferred prompts for any ambivalent releases.

## Troubleshooting

- **“Fingerprint failed – fpcalc not found”**: install `libchromaprint-tools`.
- **“Audio could not be decoded”**: ensure `ffmpeg`/`libavcodec` is installed and the format is supported.
- **Repeated prompts for the same directory**: clear the release cache (`audio-meta --reset-release-cache scan …`) if you recently renamed folders or moved files outside of the tool.
- **Organizer moves files you already tagged manually**: run `audio-meta audit --fix` once so the audit realigns directories with the tags, then future scans will skip the untouched directories (thanks to directory hashing).

## Contributing

Issues and pull requests are welcome. If you add new providers or heuristics, make sure you update the README and sample configuration so other users can benefit.

## License

This project is licensed under the [MIT License](LICENSE).
