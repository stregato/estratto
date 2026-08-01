"""SQLite state tracking: message_id -> status, so restarts don't re-process files."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

STATUS_DOWNLOADED = "downloaded"
STATUS_SORTED = "sorted"
STATUS_SCANNED = "scanned"
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    message_id INTEGER PRIMARY KEY,
    channel TEXT,
    original_filename TEXT,
    staging_path TEXT,
    final_path TEXT,
    content_type TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_scans (
    content_type TEXT PRIMARY KEY,
    first_queued_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog (
    message_id INTEGER PRIMARY KEY,
    filename TEXT,
    caption TEXT,
    size INTEGER,
    message_date TEXT,
    ext TEXT,
    source TEXT,
    indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suggested_tags (
    tag TEXT PRIMARY KEY,
    count INTEGER DEFAULT 1,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending' -- 'pending' | 'confirmed' | 'ignored'
);

CREATE TABLE IF NOT EXISTS document_tags (
    message_id INTEGER,
    tag TEXT,
    auto_tagged INTEGER DEFAULT 0, -- 1 if auto-tagged, 0 if manual
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, tag),
    FOREIGN KEY (message_id) REFERENCES catalog(message_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reading_progress (
    message_id INTEGER PRIMARY KEY,
    current_page INTEGER DEFAULT 1,
    total_pages INTEGER,
    scroll_position REAL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES catalog(message_id) ON DELETE CASCADE
);
"""


@dataclass
class FileRecord:
    message_id: int
    channel: Optional[str]
    original_filename: Optional[str]
    staging_path: Optional[str]
    final_path: Optional[str]
    content_type: Optional[str]
    status: str
    error: Optional[str]


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + a lock: the web UI drives this DB from both the
        # asyncio event loop and uvicorn's threadpool, unlike the CLI's single-thread use.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._ensure_schema()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(catalog)").fetchall()
            }
            if "source" not in columns:
                self._conn.execute("ALTER TABLE catalog ADD COLUMN source TEXT")
                self._conn.execute("UPDATE catalog SET source = 'telegram' WHERE source IS NULL")

    def _catalog_source_expr(self) -> str:
        return "COALESCE(c.source, 'telegram')"

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def get_status(self, message_id: int) -> Optional[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT status FROM files WHERE message_id = ?", (message_id,)
            )
            row = cur.fetchone()
            return row["status"] if row else None

    def is_processed(self, message_id: int) -> bool:
        """True if this message already has a stable local state and should not be reprocessed."""
        status = self.get_status(message_id)
        return status in (STATUS_DOWNLOADED, STATUS_SORTED, STATUS_SCANNED, STATUS_NEEDS_REVIEW)

    def mark_downloaded(self, message_id: int, channel: str, original_filename: str, staging_path: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO files (message_id, channel, original_filename, staging_path, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    channel=excluded.channel,
                    original_filename=excluded.original_filename,
                    staging_path=excluded.staging_path,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (message_id, channel, original_filename, staging_path, STATUS_DOWNLOADED),
            )

    def mark_sorted(self, message_id: int, final_path: str, content_type: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE files SET status = ?, final_path = ?, content_type = ?, updated_at = CURRENT_TIMESTAMP
                WHERE message_id = ?
                """,
                (STATUS_SORTED, final_path, content_type, message_id),
            )

    def mark_scanned(self, message_id: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE files SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE message_id = ?",
                (STATUS_SCANNED, message_id),
            )

    def mark_needs_review(self, message_id: int, reason: str, path: Optional[str] = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE files
                SET status = ?,
                    error = ?,
                    staging_path = COALESCE(?, staging_path),
                    final_path = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE message_id = ?
                """,
                (STATUS_NEEDS_REVIEW, reason, path, message_id),
            )

    def mark_failed(self, message_id: int, error: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE files SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE message_id = ?",
                (STATUS_FAILED, error, message_id),
            )

    def delete_file_record(self, message_id: int) -> None:
        """Delete a file record completely to reset its status."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM files WHERE message_id = ?", (message_id,))

    def get_record(self, message_id: int) -> Optional[FileRecord]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM files WHERE message_id = ?", (message_id,))
            row = cur.fetchone()
        if not row:
            return None
        return FileRecord(**{k: row[k] for k in row.keys() if k in FileRecord.__dataclass_fields__})

    def get_catalog_entry(self, message_id: int) -> Optional[dict]:
        source_expr = self._catalog_source_expr()
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT c.message_id, c.filename, c.caption, c.size, c.message_date, c.ext,
                       {source_expr} AS source
                FROM catalog c
                WHERE c.message_id = ?
                """,
                (message_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def files_pending_sort(self) -> list[FileRecord]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM files WHERE status = ?", (STATUS_DOWNLOADED,))
            rows = cur.fetchall()
        return [
            FileRecord(**{k: row[k] for k in row.keys() if k in FileRecord.__dataclass_fields__})
            for row in rows
        ]

    def files_pending_scan(self) -> list[FileRecord]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM files WHERE status = ?", (STATUS_SORTED,))
            rows = cur.fetchall()
        return [
            FileRecord(**{k: row[k] for k in row.keys() if k in FileRecord.__dataclass_fields__})
            for row in rows
        ]

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute("SELECT status, COUNT(*) AS n FROM files GROUP BY status")
            rows = cur.fetchall()
        return {row["status"]: row["n"] for row in rows}

    def recent_files(self, limit: int = 50) -> list[FileRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM files ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        return [
            FileRecord(**{k: row[k] for k in row.keys() if k in FileRecord.__dataclass_fields__})
            for row in rows
        ]

    # Debounced scan queue ---------------------------------------------------

    def queue_scan(self, content_type: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO pending_scans (content_type) VALUES (?)",
                (content_type,),
            )

    def pending_scan_types(self) -> list[str]:
        with self._lock:
            cur = self._conn.execute("SELECT content_type FROM pending_scans")
            return [row["content_type"] for row in cur.fetchall()]

    def pending_scan_age_seconds(self, content_type: str) -> Optional[float]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT (julianday('now') - julianday(first_queued_at)) * 86400.0 AS age FROM pending_scans WHERE content_type = ?",
                (content_type,),
            )
            row = cur.fetchone()
            return row["age"] if row else None

    def clear_scan_queue(self, content_type: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM pending_scans WHERE content_type = ?", (content_type,))

    # Catalog (indexed-but-not-necessarily-downloaded channel files) --------

    def upsert_catalog_entry(
        self,
        message_id: int,
        filename: str,
        caption: Optional[str],
        size: Optional[int],
        message_date: Optional[str],
        ext: str,
        source: str,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO catalog (message_id, filename, caption, size, message_date, ext, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    filename=excluded.filename,
                    caption=excluded.caption,
                    size=excluded.size,
                    message_date=excluded.message_date,
                    ext=excluded.ext,
                    source=excluded.source
                """,
                (message_id, filename, caption, size, message_date, ext, source),
            )

    def catalog(
        self,
        search: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
        downloaded_only: bool = False,
        search_any: Optional[list[str]] = None,
        source: Optional[str] = None,
    ) -> list[dict]:
        """Catalog entries left-joined with processing status, newest first."""
        source_expr = self._catalog_source_expr()
        query = """
            SELECT c.message_id, c.filename, c.caption, c.size, c.message_date, c.ext,
                   f.status, f.final_path, f.staging_path, f.error,
                   """ + source_expr + """ AS source
            FROM catalog c
            LEFT JOIN files f ON f.message_id = c.message_id
        """
        params: list = []
        where_clauses = []

        if search:
            where_clauses.append("(c.filename LIKE ? OR c.caption LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])

        if search_any:
            any_terms = [term.strip() for term in search_any if term and term.strip()]
            if any_terms:
                term_clauses = []
                for term in any_terms:
                    term_clauses.append("(c.filename LIKE ? OR c.caption LIKE ?)")
                    like = f"%{term}%"
                    params.extend([like, like])
                where_clauses.append("(" + " OR ".join(term_clauses) + ")")

        if downloaded_only:
            where_clauses.append("f.status IS NOT NULL")

        if source:
            where_clauses.append(f"{source_expr} = ?")
            params.append(source)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY c.message_date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            cur = self._conn.execute(query, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def catalog_count(
        self,
        search: Optional[str] = None,
        downloaded_only: bool = False,
        search_any: Optional[list[str]] = None,
        source: Optional[str] = None,
    ) -> int:
        source_expr = self._catalog_source_expr()
        query = "SELECT COUNT(*) AS n FROM catalog c LEFT JOIN files f ON f.message_id = c.message_id"
        params: list = []
        where_clauses = []

        if downloaded_only:
            where_clauses.append("f.status IS NOT NULL")

        if source:
            where_clauses.append(f"{source_expr} = ?")
            params.append(source)

        if search:
            where_clauses.append("(c.filename LIKE ? OR c.caption LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])

        if search_any:
            any_terms = [term.strip() for term in search_any if term and term.strip()]
            if any_terms:
                term_clauses = []
                for term in any_terms:
                    term_clauses.append("(c.filename LIKE ? OR c.caption LIKE ?)")
                    like = f"%{term}%"
                    params.extend([like, like])
                where_clauses.append("(" + " OR ".join(term_clauses) + ")")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        with self._lock:
            cur = self._conn.execute(query, params)
            return cur.fetchone()["n"]

    def last_indexed_message_id(self, source: Optional[str] = None) -> Optional[int]:
        source_expr = self._catalog_source_expr()
        query = f"""
            SELECT MAX(c.message_id) AS m
            FROM catalog c
            LEFT JOIN files f ON f.message_id = c.message_id
        """
        params: list = []
        if source:
            query += f" WHERE {source_expr} = ?"
            params.append(source)
        with self._lock:
            cur = self._conn.execute(query, params)
            row = cur.fetchone()
            return row["m"] if row and row["m"] is not None else None

    def rename_catalog_entry(self, message_id: int, new_filename: str) -> None:
        """Rename a catalog entry."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE catalog SET filename = ? WHERE message_id = ?",
                (new_filename, message_id),
            )

    # Tag management ---------------------------------------------------------

    def upsert_suggested_tag(self, tag: str) -> None:
        """Add or increment count for a suggested tag."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, count) VALUES (?, 1)
                ON CONFLICT(tag) DO UPDATE SET count = count + 1
                """,
                (tag,),
            )

    def get_suggested_tags(self, min_count: int = 3, status: str = "pending") -> list[dict]:
        """Get suggested tags with count >= min_count, ordered by count descending."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT tag, count, status FROM suggested_tags WHERE count >= ? AND status = ? ORDER BY count DESC",
                (min_count, status),
            )
            return [dict(row) for row in cur.fetchall()]

    def confirm_tag(self, tag: str) -> None:
        """Mark a tag as confirmed."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE suggested_tags SET status = 'confirmed' WHERE tag = ?",
                (tag,),
            )

    def ensure_confirmed_tag(self, tag: str) -> None:
        """Create or update a tag so it is available as a confirmed filter."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, count, status) VALUES (?, 1, 'confirmed')
                ON CONFLICT(tag) DO UPDATE SET status = 'confirmed'
                """,
                (tag,),
            )

    def ignore_tag(self, tag: str) -> None:
        """Mark a tag as ignored."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE suggested_tags SET status = 'ignored' WHERE tag = ?",
                (tag,),
            )

    def get_confirmed_tags(self) -> list[str]:
        """Get all confirmed tags."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT tag FROM suggested_tags WHERE status = 'confirmed' ORDER BY tag"
            )
            return [row["tag"] for row in cur.fetchall()]

    def tag_document(self, message_id: int, tag: str, auto_tagged: bool = False) -> None:
        """Tag a document."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO document_tags (message_id, tag, auto_tagged)
                VALUES (?, ?, ?)
                """,
                (message_id, tag, 1 if auto_tagged else 0),
            )

    def get_document_tags(self, message_id: int) -> list[str]:
        """Get all tags for a document."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT tag FROM document_tags WHERE message_id = ? ORDER BY tag",
                (message_id,),
            )
            return [row["tag"] for row in cur.fetchall()]

    def find_documents_for_tag(self, tag: str) -> list[int]:
        """Find all message_ids in catalog that contain this tag in filename or caption."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT message_id FROM catalog WHERE filename LIKE ? OR caption LIKE ?",
                (f"%{tag}%", f"%{tag}%"),
            )
            return [row["message_id"] for row in cur.fetchall()]

    def get_documents_by_tag(self, tag: str) -> list[dict]:
        """Get all documents that have been tagged with this tag."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT c.message_id, c.filename, c.caption, c.size, c.message_date, c.ext,
                       f.status, f.final_path, f.error
                FROM document_tags dt
                JOIN catalog c ON c.message_id = dt.message_id
                LEFT JOIN files f ON f.message_id = dt.message_id
                WHERE dt.tag = ?
                ORDER BY c.message_date DESC
                """,
                (tag,),
            )
            return [dict(row) for row in cur.fetchall()]

    # Reading progress -------------------------------------------------------

    def get_reading_progress(self, message_id: int) -> Optional[dict]:
        """Get reading progress for a document."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM reading_progress WHERE message_id = ?",
                (message_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def save_reading_progress(
        self,
        message_id: int,
        current_page: int = 1,
        total_pages: Optional[int] = None,
        scroll_position: float = 0,
    ) -> None:
        """Save reading progress for a document."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO reading_progress (message_id, current_page, total_pages, scroll_position, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    current_page = excluded.current_page,
                    total_pages = excluded.total_pages,
                    scroll_position = excluded.scroll_position,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (message_id, current_page, total_pages, scroll_position),
            )
