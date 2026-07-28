"""FastAPI web UI: browse/search the channel catalog, download files on demand, configure
Telegram/Kavita/OpenAI settings, and drive the Telegram login flow (phone/code/2FA) from a browser.

Runs the Telethon client inside the same asyncio event loop as the web server, so indexing,
on-demand downloads, and the optional background listener all share one connection.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import arxiv_client
from . import db as db_module
from . import tagger
from .config import Config
from .kavita_client import KavitaClient
from .main import Pipeline, _build_kavita_client, setup_logging
from .paths import static_dir
from .telegram_client import EstrattoTelegramClient

logger = logging.getLogger("estratto.web")

STATIC_DIR = static_dir()


def _resolve_config_relative_path(base_dir: Path, raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    return path


def _candidate_file_locations(cfg: Config, record: Optional[db_module.FileRecord], message_id: int) -> list[Path]:
    base_dir = cfg.path.parent.resolve()
    candidates: list[Path] = []

    if record:
        for raw_path in (record.final_path, record.staging_path):
            path = _resolve_config_relative_path(base_dir, raw_path)
            if path is not None:
                candidates.append(path)

    for directory in (cfg.staging_dir, cfg.needs_review_dir):
        search_dir = directory if directory.is_absolute() else base_dir / directory
        candidates.extend(search_dir.glob(f"*__msg{message_id}.*"))

    return candidates


def _find_existing_file(cfg: Config, record: Optional[db_module.FileRecord], message_id: int) -> Optional[Path]:
    for candidate in _candidate_file_locations(cfg, record, message_id):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _next_local_upload_id(database: db_module.Database) -> int:
    while True:
        message_id = -time.time_ns()
        if database.get_record(message_id) is None:
            return message_id


def _local_upload_filename(message_id: int, original_name: str) -> str:
    source = Path(original_name or "upload")
    safe_name = source.name or "upload"
    stem = Path(safe_name).stem or "upload"
    suffix = Path(safe_name).suffix
    return f"{stem}__local{abs(message_id)}{suffix}"


def _public_message_id(message_id: int) -> str:
    return str(message_id)


class AppState:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = db_module.Database(cfg.db_path)
        self.kavita: Optional[KavitaClient] = _build_kavita_client(cfg)
        self.pipeline = Pipeline(cfg, self.db, self.kavita)
        self.telegram = EstrattoTelegramClient(
            api_id=int(cfg.get("telegram", "api_id")),
            api_hash=str(cfg.get("telegram", "api_hash")),
            session_name=cfg.telegram_session_name,
            channel=str(cfg.get("telegram", "channel")),
            staging_dir=cfg.staging_dir,
            allowed_extensions=cfg.allowed_extensions,
        )
        self.login_phone: Optional[str] = None
        self.indexing = False
        self.index_progress = 0
        self.arxiv_downloading: set[int] = set()
        self.listen_task: Optional[asyncio.Task] = None
        self.scan_flush_task: Optional[asyncio.Task] = None
        self.background_tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def rebuild_telegram_client(self) -> None:
        """Recreate the Telethon client from current config (after api_id/hash/session/channel
        change) and reconnect, so the guided login flow works without a service restart."""
        if self.listen_task:
            self.listen_task.cancel()
            self.listen_task = None
        await self.telegram.stop()
        self.telegram = EstrattoTelegramClient(
            api_id=int(self.cfg.get("telegram", "api_id")),
            api_hash=str(self.cfg.get("telegram", "api_hash")),
            session_name=self.cfg.telegram_session_name,
            channel=str(self.cfg.get("telegram", "channel")),
            staging_dir=self.cfg.staging_dir,
            allowed_extensions=self.cfg.allowed_extensions,
        )
        await self.telegram.connect()


class PhoneBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    code: str


class PasswordBody(BaseModel):
    password: str


class AppKeysBody(BaseModel):
    api_id: str
    api_hash: str


state: Optional[AppState] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state
    cfg = Config.load(app.state.config_path)
    setup_logging(cfg)
    state = AppState(cfg)
    await state.telegram.connect()
    debounce_seconds = int(cfg.get("kavita", "scan_debounce_seconds", default=60))
    state.scan_flush_task = asyncio.create_task(
        state.pipeline.scan_debounce_loop(debounce_seconds)
    )
    logger.info("Estratto web UI ready")
    try:
        yield
    finally:
        if state.listen_task:
            state.listen_task.cancel()
        if state.scan_flush_task:
            state.scan_flush_task.cancel()
        await state.telegram.stop()
        state.db.close()


def create_app(config_path: str = None) -> FastAPI:
    import os
    if config_path is None:
        config_path = os.environ.get("ESTRATTO_CONFIG_PATH", "config.yaml")
    app = FastAPI(title="Estratto", lifespan=lifespan)
    app.state.config_path = config_path

    # ---- Status -----------------------------------------------------------

    @app.get("/api/status")
    async def status():
        authorized = await state.telegram.is_authorized()
        api_id = str(state.cfg.get("telegram", "api_id", default=""))
        api_hash = str(state.cfg.get("telegram", "api_hash", default=""))
        return {
            "telegram_authorized": authorized,
            "telegram_app_configured": bool(api_id) and bool(api_hash) and "YOUR_" not in api_hash,
            "listening": state.listen_task is not None and not state.listen_task.done(),
            "indexing": state.indexing,
            "index_progress": state.index_progress,
            "catalog_count": state.db.catalog_count(),
            "status_counts": state.db.status_counts(),
            "kavita_configured": state.kavita is not None,
            "openai_enabled": state.cfg.openai_enabled,
            "channel": state.cfg.get("telegram", "channel", default=""),
        }

    @app.get("/api/recent")
    async def recent(limit: int = 50):
        return [r.__dict__ for r in state.db.recent_files(limit)]

    # ---- Telegram login -----------------------------------------------------

    @app.post("/api/telegram/set_app_keys")
    async def set_app_keys(body: AppKeysBody):
        """Save the Telegram app's API ID/Hash and reconnect immediately, so the guided
        login flow (keys -> phone -> code -> 2FA) works in one pass without a restart."""
        api_id = body.api_id.strip()
        api_hash = body.api_hash.strip()
        if not api_id.isdigit() or not api_hash:
            raise HTTPException(400, "API ID must be numeric and API Hash must not be empty")
        state.cfg.set("telegram", "api_id", int(api_id))
        state.cfg.set("telegram", "api_hash", api_hash)
        state.cfg.save()
        try:
            await state.rebuild_telegram_client()
        except Exception as exc:
            raise HTTPException(400, f"Could not connect with these keys: {exc}") from exc
        return {"status": "saved"}

    @app.post("/api/telegram/send_code")
    async def send_code(body: PhoneBody):
        try:
            await state.telegram.send_code(body.phone)
        except Exception as exc:
            raise HTTPException(400, f"Failed to send code: {exc}") from exc
        state.login_phone = body.phone
        return {"status": "code_sent"}

    @app.post("/api/telegram/verify_code")
    async def verify_code(body: CodeBody):
        if not state.login_phone:
            raise HTTPException(400, "Call send_code first")
        try:
            result = await state.telegram.sign_in_code(state.login_phone, body.code)
        except Exception as exc:
            raise HTTPException(400, f"Login failed: {exc}") from exc
        return {"status": result}

    @app.post("/api/telegram/verify_password")
    async def verify_password(body: PasswordBody):
        try:
            await state.telegram.sign_in_password(body.password)
        except Exception as exc:
            raise HTTPException(400, f"2FA login failed: {exc}") from exc
        return {"status": "logged_in"}

    @app.post("/api/telegram/logout")
    async def logout():
        await state.telegram.log_out()
        state.login_phone = None
        await state.rebuild_telegram_client()
        return {"status": "logged_out"}

    # ---- Catalog / indexing -------------------------------------------------

    @app.get("/api/catalog")
    async def catalog(
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        downloaded_only: bool = False,
        search_any: Optional[str] = None,
    ):
        search_any_terms = [term.strip() for term in (search_any or "").split(",") if term.strip()]
        items = state.db.catalog(
            search=search,
            limit=limit,
            offset=offset,
            downloaded_only=downloaded_only,
            search_any=search_any_terms,
        )

        for item in items:
            item["message_id"] = _public_message_id(item["message_id"])
            item["file_exists"] = bool(item.get("status"))

        return {
            "items": items,
            "total": state.db.catalog_count(
                search=search,
                downloaded_only=downloaded_only,
                search_any=search_any_terms,
            ),
        }

    async def _run_index():
        state.indexing = True
        state.index_progress = 0
        try:
            def on_message(message, filename):
                caption = message.message or None
                state.db.upsert_catalog_entry(
                    message_id=message.id,
                    filename=filename,
                    caption=caption,
                    size=getattr(message.document, "size", None),
                    message_date=message.date.isoformat() if message.date else None,
                    ext=Path(filename).suffix.lower(),
                )
                state.index_progress += 1

            # Re-scan full history each time so newly-allowed extensions (for example EPUB)
            # are picked up even when their messages are older than the last indexed PDF.
            await state.telegram.index_channel(on_message, min_id=0)
        except Exception:
            logger.exception("Indexing failed")
        finally:
            state.indexing = False

    @app.post("/api/index")
    async def start_index():
        if not await state.telegram.is_authorized():
            raise HTTPException(400, "Log in to Telegram first")
        if state.indexing:
            return {"status": "already_running"}
        state.spawn(_run_index())
        return {"status": "started"}

    # ---- On-demand download --------------------------------------------------

    async def _run_download(message_id: int):
        try:
            message, path, caption = await state.telegram.download_by_message_id(message_id)
            await state.pipeline.process_file(message, path, caption)
        except Exception as exc:
            logger.exception("Download/process failed for message %s", message_id)
            state.db.mark_failed(message_id, str(exc))

    @app.post("/api/download/{message_id}")
    async def download(message_id: int):
        if not await state.telegram.is_authorized():
            raise HTTPException(400, "Log in to Telegram first")
        status_now = state.db.get_status(message_id)
        if status_now in (
            db_module.STATUS_SORTED,
            db_module.STATUS_SCANNED,
            db_module.STATUS_NEEDS_REVIEW,
        ):
            return {"status": "already_processed", "current_status": status_now}
        state.spawn(_run_download(message_id))
        return {"status": "started"}

    @app.get("/api/arxiv/search")
    async def arxiv_search(q: str = "", category: str = "", limit: int = 25, offset: int = 0):
        if not q.strip() and not category.strip():
            raise HTTPException(400, "Enter a search term or category")
        try:
            entries, total = await asyncio.to_thread(
                arxiv_client.search,
                q,
                category,
                offset,
                limit,
            )
        except Exception as exc:
            raise HTTPException(400, f"arXiv search failed: {exc}") from exc

        items = []
        for entry in entries:
            state.db.ensure_confirmed_tag("arxiv")
            state.db.tag_document(entry.doc_id, "arxiv", auto_tagged=True)
            caption_parts = [entry.summary]
            if entry.authors:
                caption_parts.append(f"Authors: {', '.join(entry.authors)}")
            caption_parts.append(f"arXiv: {entry.arxiv_id}")
            caption = "\n".join(part for part in caption_parts if part)
            state.db.upsert_catalog_entry(
                message_id=entry.doc_id,
                filename=entry.filename,
                caption=caption,
                size=None,
                message_date=arxiv_client.message_date_for_catalog(entry.published),
                ext=".pdf",
            )
            record = state.db.get_record(entry.doc_id)
            items.append({
                "message_id": _public_message_id(entry.doc_id),
                "arxiv_id": entry.arxiv_id,
                "filename": entry.filename,
                "title": entry.title,
                "summary": entry.summary,
                "authors": entry.authors,
                "message_date": entry.published,
                "status": record.status if record else "",
                "staging_path": record.staging_path if record else None,
                "final_path": record.final_path if record else None,
                "file_exists": _find_existing_file(state.cfg, record, entry.doc_id) is not None,
            })

        return {"items": items, "total": total}

    async def _run_arxiv_download(doc_id: int, arxiv_id: str, title: str, summary: str, authors: list[str], published: str):
        try:
            entry = arxiv_client.ArxivEntry(
                doc_id=doc_id,
                arxiv_id=arxiv_id,
                title=title,
                summary=summary,
                authors=authors,
                published=published,
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            )
            path = await asyncio.to_thread(arxiv_client.download_pdf, entry, state.cfg.staging_dir)
            caption_parts = [summary]
            if authors:
                caption_parts.append(f"Authors: {', '.join(authors)}")
            caption_parts.append(f"arXiv: {arxiv_id}")
            state.db.upsert_catalog_entry(
                message_id=doc_id,
                filename=entry.filename,
                caption="\n".join(part for part in caption_parts if part),
                size=path.stat().st_size if path.exists() else None,
                message_date=arxiv_client.message_date_for_catalog(published),
                ext=".pdf",
            )
            state.db.ensure_confirmed_tag("arxiv")
            state.db.tag_document(doc_id, "arxiv", auto_tagged=True)
            state.db.mark_downloaded(doc_id, "arxiv", entry.filename, str(path))
        except Exception as exc:
            logger.exception("Download/process failed for arXiv %s", arxiv_id)
            state.db.mark_failed(doc_id, str(exc))
        finally:
            state.arxiv_downloading.discard(doc_id)

    @app.post("/api/arxiv/download")
    async def arxiv_download(data: dict):
        arxiv_id = str(data.get("arxiv_id", "")).strip()
        title = str(data.get("title", "")).strip()
        summary = str(data.get("summary", "")).strip()
        authors = data.get("authors") or []
        published = str(data.get("published", "")).strip()
        if not arxiv_id or not title:
            raise HTTPException(400, "Missing arXiv document metadata")
        doc_id = int(data.get("message_id") or arxiv_client.stable_doc_id(arxiv_id))
        status_now = state.db.get_status(doc_id)
        if status_now in (
            db_module.STATUS_DOWNLOADED,
            db_module.STATUS_SORTED,
            db_module.STATUS_SCANNED,
            db_module.STATUS_NEEDS_REVIEW,
        ):
            return {"status": "already_processed", "current_status": status_now}
        if doc_id in state.arxiv_downloading:
            return {"status": "already_running"}
        state.arxiv_downloading.add(doc_id)
        state.spawn(_run_arxiv_download(doc_id, arxiv_id, title, summary, authors, published))
        return {"status": "started"}

    @app.post("/api/upload/local")
    async def upload_local_file(file: UploadFile = File(...)):
        filename = Path(file.filename or "").name
        if not filename:
            raise HTTPException(400, "Missing filename")

        state.cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        message_id = _next_local_upload_id(state.db)
        staging_path = state.cfg.staging_dir / _local_upload_filename(message_id, filename)

        try:
            with staging_path.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
        except Exception as exc:
            raise HTTPException(400, f"Could not save uploaded file: {exc}") from exc
        finally:
            await file.close()

        size = staging_path.stat().st_size if staging_path.exists() else None
        state.db.upsert_catalog_entry(
            message_id=message_id,
            filename=filename,
            caption="Uploaded from local machine",
            size=size,
            message_date=datetime.now(timezone.utc).isoformat(),
            ext=Path(filename).suffix.lower(),
        )
        state.db.mark_downloaded(message_id, "local", filename, str(staging_path))
        return {"status": "uploaded", "message_id": _public_message_id(message_id), "filename": filename}

    @app.post("/api/delete/{message_id}")
    async def delete_file(message_id: int):
        """Delete a downloaded file from disk and reset its status in the database."""
        record = state.db.get_record(message_id)
        if not record:
            raise HTTPException(404, "File not found in database")

        # Delete files from disk
        deleted_paths = []
        for path in _candidate_file_locations(state.cfg, record, message_id):
            if not path.exists():
                continue
            try:
                path.unlink()
                deleted_paths.append(str(path))
            except Exception as exc:
                logger.warning("Failed to delete file %s: %s", path, exc)

        # Remove database record to reset status
        state.db.delete_file_record(message_id)

        return {
            "status": "deleted",
            "deleted_paths": deleted_paths,
        }

    @app.post("/api/rename/{message_id}")
    async def rename_file(message_id: int, data: dict):
        """Rename a file in the catalog."""
        new_filename = data.get("filename", "").strip()
        if not new_filename:
            raise HTTPException(400, "Filename cannot be empty")

        state.db.rename_catalog_entry(message_id, new_filename)
        return {"status": "renamed", "filename": new_filename}

    # ---- Tags ----------------------------------------------------------------

    @app.get("/api/tags/extract/{message_id}")
    async def extract_tags_for_document(message_id: int):
        """Extract potential tags from a single document."""
        # Get the catalog entry
        items = state.db.catalog(limit=1, offset=0)
        # Find the specific item (this is inefficient, but works for now)
        with state.db._lock:
            cur = state.db._conn.execute(
                "SELECT filename, caption FROM catalog WHERE message_id = ?",
                (message_id,)
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(404, "Document not found")

        filename = row["filename"]
        caption = row["caption"]

        # Extract tags
        potential_tags = tagger.extract_potential_tags(filename, caption)

        # Filter out already ignored tags
        ignored_tags = set()
        with state.db._lock:
            cur = state.db._conn.execute(
                "SELECT tag FROM suggested_tags WHERE status = 'ignored'"
            )
            ignored_tags = {r["tag"] for r in cur.fetchall()}

        # Return tags that aren't ignored
        available_tags = [tag for tag in potential_tags if tag not in ignored_tags]
        return {"tags": sorted(available_tags)}

    @app.get("/api/tags/confirmed")
    async def get_confirmed_tags():
        """Get all confirmed tags."""
        return state.db.get_confirmed_tags()

    @app.post("/api/tags/confirm/{tag}")
    async def confirm_tag(tag: str, message_id: int = None):
        """Confirm a tag (adds it to filters). The filter will search all documents."""
        # Normalize tag to lowercase
        tag = tag.lower().strip()

        # Upsert the tag as confirmed
        with state.db._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, status) VALUES (?, 'confirmed')
                ON CONFLICT(tag) DO UPDATE SET status = 'confirmed'
                """,
                (tag,)
            )

        # Note: We don't tag individual documents anymore
        # The filter will do a LIKE search across all documents

        return {"status": "confirmed"}

    @app.post("/api/tags/ignore/{tag}")
    async def ignore_tag(tag: str):
        """Ignore a suggested tag (blacklist it from future suggestions)."""
        # Normalize tag to lowercase
        tag = tag.lower().strip()

        # Upsert the tag as ignored
        with state.db._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, status) VALUES (?, 'ignored')
                ON CONFLICT(tag) DO UPDATE SET status = 'ignored'
                """,
                (tag,)
            )
        return {"status": "ignored"}

    @app.get("/api/tags/documents/{tag}")
    async def get_documents_by_tag(tag: str):
        """Get all documents tagged with this tag."""
        return state.db.get_documents_by_tag(tag)

    @app.post("/api/catalog/reset")
    async def reset_catalog():
        """Clear all catalog data, tags, and document tags to start fresh."""
        with state.db._cursor() as cur:
            cur.execute("DELETE FROM catalog")
            cur.execute("DELETE FROM suggested_tags")
            cur.execute("DELETE FROM document_tags")
        return {"status": "reset"}

    # ---- Background listener ---------------------------------------------

    @app.post("/api/listen/start")
    async def listen_start():
        if not await state.telegram.is_authorized():
            raise HTTPException(400, "Log in to Telegram first")
        if state.listen_task and not state.listen_task.done():
            return {"status": "already_running"}
        state.listen_task = asyncio.create_task(
            state.telegram.listen(state.pipeline.process_file, state.pipeline.is_processed)
        )
        return {"status": "started"}

    @app.post("/api/listen/stop")
    async def listen_stop():
        if state.listen_task:
            state.listen_task.cancel()
            state.listen_task = None
        return {"status": "stopped"}

    # ---- Config -------------------------------------------------------------

    @app.get("/api/config")
    async def get_config():
        return state.cfg.as_dict_masked()

    @app.post("/api/config")
    async def save_config(patch: dict):
        _strip_masked_secrets(patch, state.cfg)
        state.cfg.update(patch)
        state.cfg.save()
        if "telegram" in patch:
            await state.rebuild_telegram_client()
        return {
            "status": "saved",
            "note": "Restart the Estratto service for staging/db path changes to take effect.",
        }

    # ---- Reading progress -----------------------------------------------------

    @app.get("/api/progress/{message_id}")
    async def get_progress(message_id: int):
        progress = state.db.get_reading_progress(message_id)
        if not progress:
            return {"current_page": 1, "total_pages": None, "scroll_position": 0}
        return progress

    @app.post("/api/progress/{message_id}")
    async def save_progress(message_id: int, data: dict):
        state.db.save_reading_progress(
            message_id=message_id,
            current_page=data.get("current_page", 1),
            total_pages=data.get("total_pages"),
            scroll_position=data.get("scroll_position", 0),
        )
        return {"status": "saved"}

    @app.get("/api/file_status/{message_id}")
    async def file_status(message_id: int):
        record = state.db.get_record(message_id)
        path_obj = _find_existing_file(state.cfg, record, message_id)
        return {"exists": path_obj is not None}

    # ---- File serving ---------------------------------------------------------

    @app.options("/api/file/{message_id}")
    async def serve_file_options(message_id: int):
        """Handle CORS preflight for file serving."""
        from fastapi.responses import Response
        response = Response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
        response.headers["Access-Control-Max-Age"] = "86400"
        return response

    @app.get("/api/file/{message_id}")
    async def serve_file(message_id: int):
        import mimetypes

        record = state.db.get_record(message_id)
        path_obj = _find_existing_file(state.cfg, record, message_id)

        if not path_obj or not path_obj.exists():
            logger.error("File not found for message_id=%s", message_id)
            logger.error("Candidate paths: %s", _candidate_file_locations(state.cfg, record, message_id))
            raise HTTPException(404, f"File not found for message_id={message_id}")

        # Guess MIME type from file extension
        mime_type, _ = mimetypes.guess_type(str(path_obj))
        if not mime_type:
            # Default MIME types for common ebook formats
            ext = path_obj.suffix.lower()
            mime_types_map = {
                '.pdf': 'application/pdf',
                '.epub': 'application/epub+zip',
                '.cbz': 'application/x-cbz',
                '.cbr': 'application/x-cbr',
            }
            mime_type = mime_types_map.get(ext, 'application/octet-stream')

        # Log detailed file information
        file_size = path_obj.stat().st_size
        logger.info(f"[File Serving] message_id={message_id}")
        logger.info(f"[File Serving] path={path_obj}")
        logger.info(f"[File Serving] size={file_size} bytes ({file_size/1024/1024:.2f} MB)")
        logger.info(f"[File Serving] mime_type={mime_type}")
        logger.info(f"[File Serving] exists={path_obj.exists()}")

        # Verify file is readable
        try:
            with open(path_obj, 'rb') as f:
                header = f.read(20)
                logger.info(f"[File Serving] First 20 bytes: {header[:20]}")
                logger.info(f"[File Serving] PDF header check: {header.startswith(b'%PDF')}")
        except Exception as e:
            logger.error(f"[File Serving] Failed to read file: {e}")
            raise HTTPException(500, f"Cannot read file: {e}")

        # Add CORS headers for PDF.js compatibility
        from fastapi.responses import FileResponse as FR
        response = FR(str(path_obj), media_type=mime_type)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Range"
        response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"
        response.headers["Cache-Control"] = "private, max-age=3600"

        logger.info(f"[File Serving] Response created successfully")
        return response

    # ---- Static frontend ------------------------------------------------------

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index_page():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/viewer")
    async def viewer_page():
        return FileResponse(str(STATIC_DIR / "viewer.html"))

    return app


def _strip_masked_secrets(patch: dict, cfg: Config, path: tuple = ()) -> None:
    """Remove masked placeholder values ("********") from a config patch so saving the
    form back doesn't clobber the real secret with the mask shown in the UI."""
    from .config import SECRET_KEYS

    for k, v in list(patch.items()):
        key_path = path + (k,)
        if isinstance(v, dict):
            _strip_masked_secrets(v, cfg, key_path)
        elif key_path in SECRET_KEYS and v == "********":
            del patch[k]
