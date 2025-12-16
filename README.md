# audio-meta

Audio metadata correction daemon for Linux libraries. The tool walks a directory tree, fingerprints audio files, attempts to fetch canonical metadata from MusicBrainz, rewrites ID3/FLAC/Vorbis tags, and applies additional heuristics for classical works. It can run as a one-off scanner or as a daemon that keeps a library tidy while new tracks arrive.

## Features

- Recursive library scanning with caching to avoid repeatedly processing the same file.
- Uses [Chromaprint](https://acoustid.org/chromaprint) fingerprints via `pyacoustid` to match files against MusicBrainz releases, with Discogs fallbacks for missing metadata.
- Normalisation pipeline that focuses on canonical artist, album artist, composer, performers, and work/movement metadata for classical music.
- Watchdog-powered daemon mode that sits in the background and processes new or modified files immediately.
- YAML configuration with per-directory overrides and rewrite rules for power users.
- Filename heuristics plus release-level memory so that once a single track in an album matches MusicBrainz, the remaining tracks inherit consistent metadata even without fingerprints.
- Optional organizer that keeps your library laid out as `/Artist/Album` (or `/Composer/Performer/Album` for classical works) and integrates with dry-run previews.

## Requirements

- Debian/Ubuntu host, Python 3.10+, `python3-venv`, `libchromaprint-tools`, and `ffmpeg`/`libav` (for codecs unsupported by your installed decoders).
- API credentials for [AcoustID](https://acoustid.org/api-key) and optionally for Discogs if you extend the provider list.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp config.sample.yaml config.yaml
audio-meta scan --config config.yaml
```

### Dry run

Preview planned tag changes without modifying files by writing them to a JSON Lines file:

```bash
audio-meta scan --config config.yaml --dry-run-output /tmp/audio-meta-preview.jsonl
```

Each line contains the resolved metadata, tag differences (`tag_changes`), and any planned relocation (`relocate_from`/`relocate_to`) so you can inspect the exact changes before running the daemon for real.

### Organizer

Enable automatic folder layout by toggling the organizer in `config.yaml`:

```yaml
organizer:
  enabled: true
  target_root: /srv/music
  classical_mixed_strategy: performer_album
```

Non-classical releases land in `/Artist/Album`. Classical albums default to `/Composer/Performer/Album`, but when multiple composers appear on the same release the strategy falls back to `/Performer/Album`. Dry-run mode previews tag changes and planned moves before touching the filesystem.

### Audit mode

If your library already contains folders that were manually edited (or were affected by a bad run in the past) you can ask the CLI to report suspicious directories without touching any files:

```bash
audio-meta audit --config config.yaml
```

The report lists each folder that appears to mix multiple album/artist combinations and/or contains duplicate track titles. It also shows example filenames so you can fix the directory manually before enabling the organizer again.

## Daemon installation

1. Copy `systemd/audio-meta.service` to `/etc/systemd/system/audio-meta.service`.
2. Adjust paths inside the service file so they point to your virtual environment and configuration file.
3. Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-meta.service
```

The daemon writes logs to journald by default.

## Architecture

- `audio_meta.config.Settings` loads YAML configuration, validates directories, and exposes API keys.
- `audio_meta.scanner.LibraryScanner` walks the filesystem and enqueues work for the processor.
- `audio_meta.providers.musicbrainz.MusicBrainzClient` coordinates fingerprint lookups, metadata searches, filename guesses, and release-level inference with confidence scoring.
- `audio_meta.providers.discogs.DiscogsClient` supplements or replaces MusicBrainz data when necessary, ensuring album/artist metadata is filled even for obscure releases.
- `audio_meta.heuristics.PathGuess` parses folder/file names into probable artist/album/track information when embedded tags are missing.
- `audio_meta.providers.musicbrainz.ReleaseTracker` caches release tracklists so sibling files can reuse the same MusicBrainz release metadata.
- `audio_meta.organizer.Organizer` determines target directories (artist/album vs composer/performer/album) and moves files accordingly once tagging succeeds.
- `audio_meta.tagging.TagWriter` coordinates reading/writing metadata using `mutagen`.
- `audio_meta.classical.ClassicalHeuristics` provides a simple scoring mechanism that distinguishes classical repertoire and rewrites metadata accordingly.
- `audio_meta.daemon.AudioMetaDaemon` runs the orchestrator and integrates with the CLI entry point.

## Next steps

The repository bootstraps a functional framework, but you will want to extend:

- Persistent caching of processed file fingerprints (sqlite).
- Discogs or streaming service providers for metadata gaps.
- More advanced classical heuristics (machine-learning or MusicBrainz work tree lookups).
- Comprehensive unit tests and CI automation.
