```
██████╗ ███████╗███████╗ ██████╗ ███╗   ██╗ █████╗ ███╗   ██╗ ██████╗███████╗
██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║██╔══██╗████╗  ██║██╔════╝██╔════╝
██████╔╝█████╗  ███████╗██║   ██║██╔██╗ ██║███████║██╔██╗ ██║██║     █████╗  
██╔══██╗██╔══╝  ╚════██║██║   ██║██║╚██╗██║██╔══██║██║╚██╗██║██║     ██╔══╝  
██║  ██║███████╗███████║╚██████╔╝██║ ╚████║██║  ██║██║ ╚████║╚██████╗███████╗
╚═╝  ╚═╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
```
**A set-and-forget curator for serious music libraries.**

Resonance cleans your metadata, fixes naming inconsistencies, and (optionally) reorganizes your files — safely, predictably, and without asking the same questions twice.

---

## Features

* Automatic music identification
* Clean, consistent tags
* Stable artist and composer names
* Optional Plex / Jellyfin-friendly folders
* Safe on large libraries
* Decisions are remembered
* Minimal ongoing maintenance

---

## Install

```bash
git clone https://github.com/your-user/resonance.git
cd resonance
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.sample.yaml config.yaml
```

Edit `config.yaml` to point to your music folders and add API keys.

---

## Use

### First run

```bash
resonance scan
```

* Wait for the scan to finish
* Answer any questions
* Then align folders with tags:

```bash
resonance audit --fix
```

---

### Daily use

```bash
resonance run
```

This scans for changes, fixes metadata, and keeps folders aligned.

If nothing changed, nothing happens.

---

## Common options (the ones you’ll actually use)

These options apply to most commands.

### `--config PATH`

Use a specific configuration file.

```bash
resonance --config music.yaml run
```

---

### `--dry-run`

Show what would change without touching any files.

Use this when you want to build confidence before your first real run.

```bash
resonance scan --dry-run
```

---

### `--log-level INFO|WARN|DEBUG`

Control how much output you see.

* `INFO` – normal use (default)
* `WARN` – only problems
* `DEBUG` – troubleshooting

```bash
resonance run --log-level WARN
```

---

### `--fix`

Apply changes instead of just reporting them.

Most audit commands are read-only unless you add this flag.

```bash
resonance audit --fix
```

---

## Useful commands at a glance

```bash
resonance scan            # identify and tag music
resonance run             # normal daily command
resonance audit           # check folder placement
resonance audit --fix     # fix folder placement
resonance singletons      # review single-track folders
```

---

## Remarks

* Resonance is designed to be predictable
* Re-running it should not cause surprises
* Your choices are remembered
* You can stop and resume at any time

This tool is meant to fade into the background.

---

## License

MIT License