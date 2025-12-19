"""
Transaction management for atomic file operations with rollback capability.

Ensures that tag writes and file moves can be rolled back if anything goes wrong,
preventing partial/inconsistent states in the library.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class FileOperation:
    """Record of a single file operation that can be rolled back."""
    op_type: str  # 'tag_write', 'move', 'delete', 'create'
    path: Path
    backup_path: Optional[Path] = None
    original_path: Optional[Path] = None  # For moves
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> dict:
        return {
            "op_type": self.op_type,
            "path": str(self.path),
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "original_path": str(self.original_path) if self.original_path else None,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> FileOperation:
        return cls(
            op_type=data["op_type"],
            path=Path(data["path"]),
            backup_path=Path(data["backup_path"]) if data.get("backup_path") else None,
            original_path=Path(data["original_path"]) if data.get("original_path") else None,
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", ""),
        )


class Transaction:
    """
    Atomic transaction for file operations with full rollback support.
    
    Usage:
        with Transaction(temp_dir) as txn:
            txn.tag_write(file_path, lambda: write_tags(file_path, tags))
            txn.move_file(src, dst)
            # Automatically commits on success or rolls back on exception
    """
    
    def __init__(self, temp_dir: Optional[Path] = None, transaction_id: Optional[str] = None):
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "audio-meta-transactions"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        self.transaction_id = transaction_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        self.txn_dir = self.temp_dir / self.transaction_id
        self.txn_dir.mkdir(parents=True, exist_ok=True)
        
        self.operations: list[FileOperation] = []
        self.committed = False
        self.rolled_back = False
        
        self.log_path = self.txn_dir / "transaction.json"
        
    def __enter__(self) -> Transaction:
        """Enter transaction context."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit transaction context - commit or rollback."""
        if exc_type is not None:
            # Exception occurred - rollback
            logger.warning(
                "Transaction %s failed with %s: %s - rolling back",
                self.transaction_id,
                exc_type.__name__ if exc_type else "unknown",
                exc_val,
            )
            self.rollback()
            return False  # Re-raise the exception
        
        # No exception - commit
        try:
            self.commit()
        except Exception as commit_exc:
            logger.error("Commit failed for transaction %s: %s", self.transaction_id, commit_exc)
            self.rollback()
            raise
        
        return False
    
    def tag_write(self, path: Path, write_func: Callable[[], None]) -> None:
        """
        Perform a tag write operation with backup.
        
        Args:
            path: File to modify
            write_func: Function that writes the tags (should modify file in-place)
        """
        if not path.exists():
            raise FileNotFoundError(f"Cannot write tags to non-existent file: {path}")
        
        # Create backup
        backup_path = self.txn_dir / f"backup_{len(self.operations)}_{path.name}"
        shutil.copy2(path, backup_path)
        
        # Record operation
        op = FileOperation(
            op_type="tag_write",
            path=path,
            backup_path=backup_path,
            metadata={"size": path.stat().st_size},
        )
        self.operations.append(op)
        self._save_log()
        
        # Perform write
        try:
            write_func()
        except Exception as exc:
            logger.error("Tag write failed for %s: %s", path, exc)
            raise
    
    def move_file(self, src: Path, dst: Path) -> None:
        """
        Move a file atomically with rollback support.
        
        Args:
            src: Source path
            dst: Destination path
        """
        if not src.exists():
            raise FileNotFoundError(f"Cannot move non-existent file: {src}")
        
        # Ensure destination directory exists
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        # If destination exists, back it up
        dst_backup = None
        if dst.exists():
            dst_backup = self.txn_dir / f"dst_backup_{len(self.operations)}_{dst.name}"
            shutil.copy2(dst, dst_backup)
        
        # Record operation BEFORE performing it
        op = FileOperation(
            op_type="move",
            path=dst,
            original_path=src,
            backup_path=dst_backup,
            metadata={"size": src.stat().st_size},
        )
        self.operations.append(op)
        self._save_log()
        
        # Perform move
        try:
            shutil.move(str(src), str(dst))
        except Exception as exc:
            logger.error("Move failed from %s to %s: %s", src, dst, exc)
            # Remove the operation we just added
            self.operations.pop()
            raise
    
    def create_file(self, path: Path, content: bytes) -> None:
        """
        Create a new file.
        
        Args:
            path: Path to create
            content: File content
        """
        if path.exists():
            raise FileExistsError(f"Cannot create existing file: {path}")
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        op = FileOperation(
            op_type="create",
            path=path,
            metadata={"size": len(content)},
        )
        self.operations.append(op)
        self._save_log()
        
        try:
            path.write_bytes(content)
        except Exception as exc:
            logger.error("Create file failed for %s: %s", path, exc)
            self.operations.pop()
            raise
    
    def delete_file(self, path: Path) -> None:
        """
        Delete a file with backup.
        
        Args:
            path: File to delete
        """
        if not path.exists():
            logger.warning("Cannot delete non-existent file: %s", path)
            return
        
        # Create backup
        backup_path = self.txn_dir / f"deleted_{len(self.operations)}_{path.name}"
        shutil.copy2(path, backup_path)
        
        op = FileOperation(
            op_type="delete",
            path=path,
            backup_path=backup_path,
            metadata={"size": path.stat().st_size},
        )
        self.operations.append(op)
        self._save_log()
        
        try:
            path.unlink()
        except Exception as exc:
            logger.error("Delete failed for %s: %s", path, exc)
            self.operations.pop()
            raise
    
    def commit(self) -> None:
        """Commit the transaction - just cleanup temp files."""
        if self.committed:
            return
        
        logger.debug("Committing transaction %s (%d operations)", self.transaction_id, len(self.operations))
        
        # Clean up transaction directory
        try:
            shutil.rmtree(self.txn_dir)
        except Exception as exc:
            logger.warning("Failed to cleanup transaction dir %s: %s", self.txn_dir, exc)
        
        self.committed = True
    
    def rollback(self) -> None:
        """Rollback all operations in reverse order."""
        if self.rolled_back:
            return
        
        logger.warning("Rolling back transaction %s (%d operations)", self.transaction_id, len(self.operations))
        
        # Process operations in reverse order
        for op in reversed(self.operations):
            try:
                self._rollback_operation(op)
            except Exception as exc:
                logger.error("Failed to rollback operation %s: %s", op.op_type, exc)
                # Continue trying to rollback other operations
        
        self.rolled_back = True
        
        # Keep the transaction log for forensics
        logger.info("Transaction %s rolled back - log preserved at %s", self.transaction_id, self.log_path)
    
    def _rollback_operation(self, op: FileOperation) -> None:
        """Rollback a single operation."""
        if op.op_type == "tag_write":
            # Restore from backup
            if op.backup_path and op.backup_path.exists():
                shutil.copy2(op.backup_path, op.path)
                logger.debug("Restored %s from backup", op.path)
        
        elif op.op_type == "move":
            # Move back to original location
            if op.path.exists() and op.original_path:
                op.path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(op.path), str(op.original_path))
                logger.debug("Moved %s back to %s", op.path, op.original_path)
            
            # Restore destination if it was overwritten
            if op.backup_path and op.backup_path.exists():
                shutil.copy2(op.backup_path, op.path)
                logger.debug("Restored overwritten destination %s", op.path)
        
        elif op.op_type == "create":
            # Delete created file
            if op.path.exists():
                op.path.unlink()
                logger.debug("Deleted created file %s", op.path)
        
        elif op.op_type == "delete":
            # Restore deleted file
            if op.backup_path and op.backup_path.exists():
                op.path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(op.backup_path, op.path)
                logger.debug("Restored deleted file %s", op.path)
    
    def _save_log(self) -> None:
        """Save transaction log to disk."""
        try:
            log_data = {
                "transaction_id": self.transaction_id,
                "timestamp": datetime.utcnow().isoformat(),
                "operations": [op.to_dict() for op in self.operations],
            }
            self.log_path.write_text(json.dumps(log_data, indent=2))
        except Exception as exc:
            logger.warning("Failed to save transaction log: %s", exc)


class TransactionManager:
    """Manages recovery of incomplete transactions."""
    
    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "audio-meta-transactions"
    
    def recover_incomplete(self) -> int:
        """
        Recover any incomplete transactions found in temp directory.
        
        Returns the number of transactions recovered.
        """
        if not self.temp_dir.exists():
            return 0
        
        recovered = 0
        for txn_dir in self.temp_dir.iterdir():
            if not txn_dir.is_dir():
                continue
            
            log_path = txn_dir / "transaction.json"
            if not log_path.exists():
                continue
            
            try:
                # Load transaction
                log_data = json.loads(log_path.read_text())
                txn_id = log_data.get("transaction_id")
                
                logger.warning("Found incomplete transaction %s - rolling back", txn_id)
                
                # Recreate transaction and rollback
                txn = Transaction(self.temp_dir, txn_id)
                for op_data in log_data.get("operations", []):
                    txn.operations.append(FileOperation.from_dict(op_data))
                
                txn.rollback()
                recovered += 1
                
            except Exception as exc:
                logger.error("Failed to recover transaction in %s: %s", txn_dir, exc)
        
        return recovered
