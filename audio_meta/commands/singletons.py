from __future__ import annotations

from ..audit import LibraryAuditor


def run(auditor: LibraryAuditor) -> None:
    entries = auditor.collect_singletons()
    entries = [entry for entry in entries if entry.file_path.exists()]
    if not entries:
        print("No single-file directories detected.")
        return
    total = len(entries)
    for idx, entry in enumerate(entries, 1):
        directory_label = auditor.display_path(entry.directory)
        print(f"\n[{idx}/{total}] {directory_label}")
        print(f"    File: {entry.file_path.name}")
        print(f"    Artist: {entry.meta.artist or '<unknown>'}")
        print(f"    Album: {entry.meta.album or '<unknown>'}")
        if entry.meta.composer:
            print(f"    Composer: {entry.meta.composer}")
        print(f"    Title: {entry.meta.title or '<unknown>'}")
        if entry.release_id:
            print(f"    Release ID: {entry.release_id}")
        if entry.target:
            target_label = auditor.display_path(entry.target.parent)
            print(f"    Suggested target: {target_label}/{entry.target.name}")
        elif entry.release_home:
            home_label = auditor.display_path(entry.release_home)
            print(f"    Release home: {home_label}/ (already contains other tracks)")
        elif entry.canonical_path:
            canonical_label = auditor.display_path(entry.canonical_path.parent)
            print(f"    Canonical path: {canonical_label}/{entry.canonical_path.name}")
        else:
            print("    Suggested target: (already in place or unknown)")
        while True:
            choice = input("Action [k]eep/[m]ove/[d]elete/[i]ignore/[q]uit: ").strip().lower()
            if choice in {"", "k"}:
                break
            if choice == "q":
                print("Stopping singleton review.")
                return
            if choice == "m":
                destination = entry.target
                if not destination and entry.release_home:
                    destination = entry.release_home / entry.file_path.name
                if not destination:
                    print("No suggested destination; keeping file in place.")
                    break
                auditor.organizer.move(entry.meta, destination, dry_run=False)
                auditor.organizer.cleanup_source_directory(entry.directory)
                entry.file_path = entry.meta.path
                entry.directory = entry.meta.path.parent
                print("Moved to", auditor.display_path(entry.meta.path.parent))
                break
            if choice == "d":
                try:
                    entry.file_path.unlink()
                    try:
                        entry.directory.rmdir()
                    except OSError:
                        pass
                    print("Deleted file (and directory if empty).")
                except FileNotFoundError:
                    print("File already missing.")
                break
            if choice == "i":
                auditor.cache.ignore_directory(entry.directory, "user ignored singleton")
                print("Directory will be ignored in future single-file audits.")
                break
            print("Invalid choice. Use k/m/d/i/q.")

