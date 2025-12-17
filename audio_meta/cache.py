from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Optional


class MetadataCache:
    """Simple SQLite-backed cache for expensive provider lookups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(namespace, key)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                organized INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        try:
            self._conn.execute("ALTER TABLE processed_files ADD COLUMN organized INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS moves (
                source_path TEXT PRIMARY KEY,
                target_path TEXT NOT NULL,
                moved_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS directory_releases (
                directory_path TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                release_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS directory_hashes (
                directory_path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hash_releases (
                directory_hash TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                release_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS release_layouts (
                release_key TEXT PRIMARY KEY,
                layout TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS release_homes (
                release_key TEXT PRIMARY KEY,
                directory_path TEXT NOT NULL,
                track_count INTEGER,
                directory_hash TEXT,
                updated_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_names (
                token TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ignored_directories (
                directory_path TEXT PRIMARY KEY,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deferred_prompts (
                directory_path TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_recording(self, recording_id: str) -> Optional[dict]:
        return self._get("recording", recording_id)

    def set_recording(self, recording_id: str, value: dict) -> None:
        self._set("recording", recording_id, value)

    def get_release(self, release_id: str) -> Optional[dict]:
        return self._get("release", release_id)

    def set_release(self, release_id: str, value: dict) -> None:
        self._set("release", release_id, value)

    def get_discogs_release(self, release_id: str | int) -> Optional[dict]:
        return self._get("discogs_release", str(release_id))

    def set_discogs_release(self, release_id: str | int, value: dict) -> None:
        self._set("discogs_release", str(release_id), value)

    def get_discogs_search(self, key: str) -> Optional[dict]:
        return self._get("discogs_search", key)

    def set_discogs_search(self, key: str, value: Optional[dict]) -> None:
        payload = value or {}
        self._set("discogs_search", key, payload)

    def get_processed_file(self, path: Path) -> Optional[tuple[int, int, bool]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT mtime_ns, size_bytes, organized FROM processed_files WHERE path = ?",
                (str(path),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        mtime, size, organized = row
        return int(mtime), int(size), bool(organized)

    def set_processed_file(self, path: Path, mtime_ns: int, size_bytes: int, organized: bool) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO processed_files(path, mtime_ns, size_bytes, organized)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size_bytes=excluded.size_bytes, organized=excluded.organized
                """,
                (str(path), int(mtime_ns), int(size_bytes), 1 if organized else 0),
            )
            self._conn.commit()

    def record_move(self, source: Path, target: Path) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO moves(source_path, target_path, moved_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_path) DO UPDATE SET target_path=excluded.target_path, moved_at=excluded.moved_at
                """,
                (str(source), str(target)),
            )
            self._conn.commit()

    def get_move(self, source: Path) -> Optional[Path]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT target_path FROM moves WHERE source_path = ?",
                (str(source),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return Path(row[0])

    def clear_moves(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM moves")
            self._conn.execute("UPDATE processed_files SET organized = 0")
            self._conn.commit()

    def list_moves(self) -> list[tuple[str, str]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT source_path, target_path FROM moves ORDER BY moved_at DESC"
            )
            rows = cursor.fetchall()
        return [(row[0], row[1]) for row in rows]

    def delete_move(self, source: Path | str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM moves WHERE source_path = ?", (str(source),))
            self._conn.commit()

    def clear_directory_releases(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM directory_releases")
            self._conn.execute("DELETE FROM directory_hashes")
            self._conn.execute("DELETE FROM hash_releases")
            self._conn.execute("DELETE FROM release_layouts")
            self._conn.execute("DELETE FROM release_homes")
            self._conn.execute("DELETE FROM deferred_prompts")
            self._conn.commit()

    def get_directory_release(self, directory: Path | str) -> Optional[tuple[str, str, float]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT provider, release_id, score FROM directory_releases WHERE directory_path = ?",
                (str(directory),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        provider, release_id, score = row
        return provider, release_id, float(score)

    def set_directory_release(self, directory: Path | str, provider: str, release_id: str, score: float) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO directory_releases(directory_path, provider, release_id, score, updated_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(directory_path)
                DO UPDATE SET provider=excluded.provider, release_id=excluded.release_id, score=excluded.score, updated_at=excluded.updated_at
                """,
                (str(directory), provider, release_id, float(score)),
            )
            self._conn.commit()

    def delete_directory_release(self, directory: Path | str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM directory_releases WHERE directory_path = ?",
                (str(directory),),
            )
            self._conn.commit()

    def find_directories_for_release(self, release_id: str) -> list[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT directory_path FROM directory_releases WHERE release_id = ?",
                (release_id,),
            )
            rows = cursor.fetchall()
        return [row[0] for row in rows]

    def get_directory_hash(self, directory: Path | str) -> Optional[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT hash FROM directory_hashes WHERE directory_path = ?",
                (str(directory),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return row[0]

    def set_directory_hash(self, directory: Path | str, hash_value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO directory_hashes(directory_path, hash, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(directory_path)
                DO UPDATE SET hash=excluded.hash, updated_at=excluded.updated_at
                """,
                (str(directory), hash_value),
            )
            self._conn.commit()

    def get_release_by_hash(self, directory_hash: str) -> Optional[tuple[str, str, float]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT provider, release_id, score FROM hash_releases WHERE directory_hash = ?",
                (directory_hash,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        provider, release_id, score = row
        return provider, release_id, float(score)

    def set_release_by_hash(self, directory_hash: str, provider: str, release_id: str, score: float) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hash_releases(directory_hash, provider, release_id, score, updated_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(directory_hash)
                DO UPDATE SET provider=excluded.provider, release_id=excluded.release_id, score=excluded.score, updated_at=excluded.updated_at
                """,
                (directory_hash, provider, release_id, float(score)),
            )
            self._conn.commit()

    def ignore_directory(self, directory: Path | str, reason: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ignored_directories(directory_path, reason, created_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(directory_path) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (str(directory), reason),
            )
            self._conn.commit()

    def unignore_directory(self, directory: Path | str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM ignored_directories WHERE directory_path = ?",
                (str(directory),),
            )
            self._conn.commit()

    def is_directory_ignored(self, directory: Path | str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM ignored_directories WHERE directory_path = ?",
                (str(directory),),
            )
            row = cursor.fetchone()
        return bool(row)

    def _get(self, namespace: str, key: str) -> Optional[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM cache WHERE namespace = ? AND key = ?", (namespace, key)
            )
            row = cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def _set(self, namespace: str, key: str, value: Any) -> None:
        payload = json.dumps(value)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cache(namespace, key, value)
                VALUES(?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value
                """,
                (namespace, key, payload),
            )
            self._conn.commit()

    def add_deferred_prompt(self, directory: Path | str, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO deferred_prompts(directory_path, reason, created_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(directory_path)
                DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (str(directory), reason),
            )
            self._conn.commit()

    def remove_deferred_prompt(self, directory: Path | str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM deferred_prompts WHERE directory_path = ?",
                (str(directory),),
            )
            self._conn.commit()

    def list_deferred_prompts(self) -> list[tuple[str, str]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT directory_path, reason FROM deferred_prompts ORDER BY created_at"
            )
            rows = cursor.fetchall()
        return [(row[0], row[1]) for row in rows]

    def get_release_layout(self, release_key: str) -> Optional[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT layout FROM release_layouts WHERE release_key = ?",
                (release_key,),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def set_release_layout(self, release_key: str, layout: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO release_layouts(release_key, layout, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(release_key) DO UPDATE SET layout=excluded.layout, updated_at=excluded.updated_at
                """,
                (release_key, layout),
            )
            self._conn.commit()

    def get_release_home(self, release_key: str) -> Optional[tuple[str, Optional[int], Optional[str]]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT directory_path, track_count, directory_hash FROM release_homes WHERE release_key = ?",
                (release_key,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        directory_path, track_count, directory_hash = row
        count = int(track_count) if track_count is not None else None
        return str(directory_path), count, (str(directory_hash) if directory_hash else None)

    def set_release_home(
        self,
        release_key: str,
        directory: Path | str,
        track_count: Optional[int] = None,
        directory_hash: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO release_homes(release_key, directory_path, track_count, directory_hash, updated_at, last_seen)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(release_key)
                DO UPDATE SET
                    directory_path=excluded.directory_path,
                    track_count=excluded.track_count,
                    directory_hash=excluded.directory_hash,
                    updated_at=excluded.updated_at,
                    last_seen=excluded.last_seen
                """,
                (release_key, str(directory), track_count, directory_hash),
            )
            self._conn.commit()

    def delete_release_home(self, release_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM release_homes WHERE release_key = ?",
                (release_key,),
            )
            self._conn.commit()

    def get_canonical_name(self, token: str) -> Optional[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM canonical_names WHERE token = ?",
                (token,),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def set_canonical_name(self, token: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO canonical_names(token, value, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(token) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (token, value),
            )
            self._conn.commit()
