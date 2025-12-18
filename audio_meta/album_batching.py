from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .directory_identity import looks_like_disc_folder
from .scanner import DirectoryBatch, LibraryScanner


@dataclass(slots=True)
class AlbumBatcher:
    scanner: LibraryScanner
    processed_albums: set[Path]

    @dataclass(slots=True)
    class Result:
        album_root: Path
        batch: Optional[DirectoryBatch]
        already_processed: bool = False

    def prepare_album_batch(
        self,
        batch: DirectoryBatch,
        *,
        force_prompt: bool = False,
    ) -> "AlbumBatcher.Result":
        directory = batch.directory
        album_root = self.album_root(directory)
        try:
            resolved_root = album_root.resolve()
        except FileNotFoundError:
            resolved_root = album_root
        if not force_prompt and resolved_root in self.processed_albums:
            return AlbumBatcher.Result(
                album_root=album_root, batch=None, already_processed=True
            )
        self.processed_albums.add(resolved_root)
        disc_dirs = self.disc_directories(album_root)
        files: list[Path] = []
        seen: set[Path] = set()

        def _add_files(paths: list[Path]) -> None:
            for path in paths:
                if path not in seen:
                    files.append(path)
                    seen.add(path)

        if album_root == directory:
            _add_files(batch.files)
        else:
            root_batch = self.scanner.collect_directory(album_root)
            if root_batch:
                _add_files(root_batch.files)
        for disc_dir in disc_dirs:
            if disc_dir == directory:
                _add_files(batch.files)
            else:
                sub_batch = self.scanner.collect_directory(disc_dir)
                if sub_batch:
                    _add_files(sub_batch.files)
        if not files:
            return AlbumBatcher.Result(
                album_root=album_root, batch=None, already_processed=False
            )
        return AlbumBatcher.Result(
            album_root=album_root,
            batch=DirectoryBatch(directory=album_root, files=files),
            already_processed=False,
        )

    @staticmethod
    def album_root(directory: Path) -> Path:
        if looks_like_disc_folder(directory.name) and directory.parent != directory:
            return directory.parent
        return directory

    @staticmethod
    def disc_directories(album_root: Path) -> list[Path]:
        discs: list[Path] = []
        try:
            entries = list(album_root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return discs
        for entry in entries:
            if entry.is_dir() and looks_like_disc_folder(entry.name):
                discs.append(entry)
        return sorted(discs)
