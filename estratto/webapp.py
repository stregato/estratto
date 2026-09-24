"""FastAPI web UI: browse/search the catalog, download files on demand, configure
Telegram/OpenAI settings, and drive the Telegram login flow (phone/code/2FA) from a browser.

Runs the Telethon client inside the same asyncio event loop as the web server, so indexing,
on-demand downloads, and the optional background listener all share one connection.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
import requests

from . import auth
from . import arxiv_client
from . import db as db_module
from . import tagger
from .config import Config
from .config import DEFAULT_TELEGRAM_API_HASH
from .config import DEFAULT_TELEGRAM_API_ID
from .main import Pipeline, setup_logging
from .paths import static_dir
from .profiles import (
    ProfileError,
    ProfileStore,
    profile_hash,
)
from .telegram_client import EstrattoTelegramClient

logger = logging.getLogger("estratto.web")

STATIC_DIR = static_dir()
TELEGRAM_API_ID_HEADER = "X-Estratto-Telegram-Api-Id"
TELEGRAM_API_HASH_HEADER = "X-Estratto-Telegram-Api-Hash"


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
        if candidate.exists():
            return candidate
    return None


def _parse_range_header(range_header: Optional[str], file_size: int) -> Optional[tuple[int, int]]:
    if not range_header:
        return None
    if not range_header.startswith("bytes="):
        return None

    spec = range_header[len("bytes="):].strip()
    if "," in spec:
        raise HTTPException(416, "Multiple ranges are not supported")

    start_text, _, end_text = spec.partition("-")
    if not start_text and not end_text:
        return None

    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise HTTPException(416, "Invalid range")
        start = max(0, file_size - length)
        end = file_size - 1
        return start, end

    start = int(start_text)
    end = int(end_text) if end_text else file_size - 1
    if start < 0 or end < start or start >= file_size:
        raise HTTPException(416, "Range not satisfiable")
    return start, min(end, file_size - 1)


def _fetch_website_title(url: str, timeout: int = 8) -> str:
    parsed = requests.utils.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "Only http and https URLs are supported")

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Estratto/1.0 (+website title fetch)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(400, f"Could not fetch website: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if not title:
        raise HTTPException(404, "No page title found")
    return title


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


class ProfileConfigView:
    def __init__(self, base_cfg: Config, settings: dict[str, Any], store: ProfileStore):
        self.base_cfg = base_cfg
        self.settings = settings
        self.store = store

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.settings
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return self.base_cfg.get(*keys, default=default)
            node = node[key]
        return node

    @property
    def path(self) -> Path:
        return self.base_cfg.path

    @property
    def staging_dir(self) -> Path:
        return self.store.temp_dir

    @property
    def needs_review_dir(self) -> Path:
        return self.store.root_dir / "needs-review"

    @property
    def allowed_extensions(self) -> set[str]:
        return self.base_cfg.allowed_extensions

    @property
    def openai_enabled(self) -> bool:
        return bool(self.get("openai", "enabled", default=self.base_cfg.openai_enabled))


@dataclass
class ProfileRuntime:
    store: ProfileStore
    db: db_module.Database
    settings: dict[str, Any]
    pipeline: Pipeline
    telegram_client: Optional[EstrattoTelegramClient] = None
    telegram_client_credentials: Optional[tuple[str, str]] = None
    login_phone: Optional[str] = None
    index_progress: int = 0
    indexing: bool = False
    arxiv_downloading: set[int] = field(default_factory=set)
    telegram_downloading: set[int] = field(default_factory=set)
    listen_task: Optional[asyncio.Task] = None

    @property
    def profile_hash(self) -> str:
        return self.store.hash


class AppState:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.auth_path = cfg.path.parent.resolve() / "accounts.db"
        auth.initialize(self.auth_path)
        self.profile_runtimes: dict[str, ProfileRuntime] = {}
        self.background_tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    def runtime_for_profile(self, profile_name: str) -> ProfileRuntime:
        store = ProfileStore.from_profile(self.cfg.path.parent.resolve(), profile_name)
        runtime = self.profile_runtimes.get(store.hash)
        if runtime is not None:
            runtime.settings = store.load_settings(profile_name)
            runtime.pipeline.cfg = ProfileConfigView(self.cfg, runtime.settings, store)
            return runtime

        settings = store.load_settings(profile_name)
        db = db_module.Database(store.db_path)
        profile_cfg = ProfileConfigView(self.cfg, settings, store)
        runtime = ProfileRuntime(
            store=store,
            db=db,
            settings=settings,
            pipeline=Pipeline(profile_cfg, db),
        )
        self.profile_runtimes[store.hash] = runtime
        return runtime

    def runtime_for_hash(self, profile_hash_value: str) -> ProfileRuntime:
        store = ProfileStore(self.cfg.path.parent.resolve(), profile_hash_value)
        runtime = self.profile_runtimes.get(store.hash)
        if runtime is not None:
            return runtime
        db = db_module.Database(store.db_path)
        runtime = ProfileRuntime(
            store=store,
            db=db,
            settings={},
            pipeline=Pipeline(ProfileConfigView(self.cfg, {}, store), db),
        )
        self.profile_runtimes[store.hash] = runtime
        return runtime

    def save_settings(self, runtime: ProfileRuntime, profile_name: str) -> None:
        runtime.store.save_settings(profile_name, runtime.settings)
        runtime.pipeline.cfg = ProfileConfigView(self.cfg, runtime.settings, runtime.store)

    def telegram_settings(self, runtime: ProfileRuntime) -> dict:
        row = runtime.settings.get("telegram") or {}
        return {
            "channel": str(row.get("channel") or self.cfg.get("telegram", "channel", default="")).strip(),
        }

    def telegram_credentials(self, request: Optional[Request] = None, override: Optional[dict[str, str]] = None) -> dict[str, str]:
        if override is not None:
            return {
                "api_id": str(override.get("api_id") or "").strip(),
                "api_hash": str(override.get("api_hash") or "").strip(),
            }
        if request is not None:
            api_id = str(request.headers.get(TELEGRAM_API_ID_HEADER) or "").strip()
            api_hash = str(request.headers.get(TELEGRAM_API_HASH_HEADER) or "").strip()
            if api_id and api_hash and "YOUR_" not in api_hash:
                return {"api_id": api_id, "api_hash": api_hash}

        return {
            "api_id": str(DEFAULT_TELEGRAM_API_ID),
            "api_hash": DEFAULT_TELEGRAM_API_HASH,
        }

    def telegram_app_configured(self, request: Optional[Request] = None) -> bool:
        credentials = self.telegram_credentials(request)
        api_id = credentials["api_id"]
        api_hash = credentials["api_hash"]
        return bool(api_id) and bool(api_hash) and "YOUR_" not in api_hash

    async def get_telegram_client(
        self,
        runtime: ProfileRuntime,
        request: Optional[Request] = None,
        *,
        rebuild: bool = False,
        credentials_override: Optional[dict[str, str]] = None,
    ) -> EstrattoTelegramClient:
        credentials = self.telegram_credentials(request, credentials_override)
        runtime.telegram_client_credentials = (
            credentials["api_id"],
            credentials["api_hash"],
        )
        settings = self.telegram_settings(runtime)
        if not credentials["api_id"] or not credentials["api_hash"] or "YOUR_" in credentials["api_hash"]:
            runtime.telegram_client_credentials = None
            raise HTTPException(400, "Telegram app credentials are not configured for this session")

        if rebuild:
            await self.stop_telegram_client(runtime)

        client = runtime.telegram_client
        if client is None:
            client = EstrattoTelegramClient(
                api_id=int(credentials["api_id"]),
                api_hash=credentials["api_hash"],
                session_name=runtime.store.telegram_session_name(),
                channel=settings["channel"],
                staging_dir=runtime.store.temp_dir,
                allowed_extensions=self.cfg.allowed_extensions,
            )
            runtime.telegram_client = client
        await client.connect()
        return client

    async def stop_telegram_client(self, runtime: ProfileRuntime) -> None:
        task = runtime.listen_task
        runtime.listen_task = None
        if task:
            task.cancel()
        client = runtime.telegram_client
        runtime.telegram_client = None
        runtime.telegram_client_credentials = None
        if client is not None:
            await client.stop()

    async def stop_all_telegram_clients(self) -> None:
        for runtime in list(self.profile_runtimes.values()):
            if runtime.listen_task:
                runtime.listen_task.cancel()
                runtime.listen_task = None
            if runtime.telegram_client is not None:
                client = runtime.telegram_client
                runtime.telegram_client = None
                await client.stop()
            runtime.db.close()


class PhoneBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    code: str


class PasswordBody(BaseModel):
    password: str


class AppKeysBody(BaseModel):
    api_id: str
    api_hash: str


class WorkspaceStateBody(BaseModel):
    open_documents: list[dict]
    active_document_id: Optional[str] = None


class DocumentStateBody(BaseModel):
    document_id: str
    document_kind: str = "file"
    current_page: int = 1
    total_pages: Optional[int] = None
    scroll_position: Union[str, int, float] = "0"
    viewer_prefs: dict = Field(default_factory=dict)


class AiAskBody(BaseModel):
    selection_text: str
    action: str = "explain_simple"
    question: Optional[str] = None
    source: Optional[str] = None


state: Optional[AppState] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state
    cfg = Config.load(app.state.config_path)
    setup_logging(cfg)
    state = AppState(cfg)

    async def cleanup_loop():
        while True:
            try:
                count = await asyncio.to_thread(auth.cleanup_inactive_files, state.auth_path, cfg.path.parent.resolve())
                if count:
                    logger.info("Cleaned stored files for %s inactive accounts", count)
            except Exception:
                logger.exception("Inactive file cleanup failed; will retry tomorrow")
            await asyncio.sleep(24 * 60 * 60)

    cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info("Estratto web UI ready")
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await state.stop_all_telegram_clients()


def create_app(config_path: str = None) -> FastAPI:
    import os
    if config_path is None:
        config_path = os.environ.get("ESTRATTO_CONFIG_PATH", "config.yaml")
    app = FastAPI(title="Estratto", lifespan=lifespan)
    app.state.config_path = config_path

    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _settings_section(runtime: ProfileRuntime, key: str) -> dict[str, Any]:
        value = runtime.settings.get(key)
        if not isinstance(value, dict):
            value = {}
            runtime.settings[key] = value
        return value

    def _merge_nested(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                _merge_nested(dst[key], value)
            else:
                dst[key] = value

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        public = {"/api/auth/email", "/api/auth/login"}
        if request.url.path.startswith("/api/") and request.url.path not in public:
            from fastapi.responses import JSONResponse
            try:
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                request.state.account = await asyncio.to_thread(auth.session, state.auth_path, token)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/auth/email")
    def check_email(data: dict):
        email = auth.normalize_email(data.get("email"))
        with auth.connect(state.auth_path) as db:
            exists = db.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone()
        return {"email": email, "registered": bool(exists)}

    @app.post("/api/auth/login")
    def login(data: dict, request: Request):
        return auth.authenticate(state.auth_path, data.get("email"), data.get("pin"),
                                 data.get("register") is True,
                                 request.client.host if request.client else "local")

    @app.post("/api/auth/logout")
    def logout(request: Request):
        auth.logout(state.auth_path, request.headers.get("Authorization", "").removeprefix("Bearer "))
        return {"status": "ok"}

    def _profile_name_from_request(request: Request) -> str:
        return request.state.account["profile_secret"]

    def _profile_hash_from_request(request: Request) -> str:
        return profile_hash(_profile_name_from_request(request))

    def _require_runtime(request: Request) -> ProfileRuntime:
        try:
            return state.runtime_for_profile(_profile_name_from_request(request))
        except ProfileError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _finalize_download(runtime: ProfileRuntime, profile_name: str, stored_name: str, source_path: Path) -> str:
        encrypted_path = runtime.store.encrypt_file(
            profile_name,
            source_path,
            stored_name,
            chunk_size=state.cfg.encryption_chunk_size,
        )
        if source_path.exists():
            source_path.unlink()
        return str(encrypted_path)

    def _runtime_for_chunk_request(request: Request) -> ProfileRuntime:
        raw_hash = _profile_hash_from_request(request)
        try:
            return state.runtime_for_hash(raw_hash)
        except Exception as exc:
            raise HTTPException(400, f"Invalid profile hash: {exc}") from exc

    def _document_state_payload(runtime: ProfileRuntime, document_id: str) -> Optional[dict[str, Any]]:
        documents = _settings_section(runtime, "documents")
        payload = documents.get(document_id)
        return payload if isinstance(payload, dict) else None

    # ---- Status -----------------------------------------------------------

    @app.get("/api/status")
    async def status(request: Request):
        runtime = _require_runtime(request)
        authorized = False
        app_configured = state.telegram_app_configured(request)
        settings = state.telegram_settings(runtime)
        listening = runtime.listen_task is not None and not runtime.listen_task.done()
        if app_configured:
            try:
                authorized = await (await state.get_telegram_client(runtime, request)).is_authorized()
            except Exception:
                authorized = False
        return {
            "telegram_authorized": authorized,
            "telegram_app_configured": app_configured,
            "listening": listening,
            "indexing": runtime.indexing,
            "index_progress": runtime.index_progress,
            "catalog_count": runtime.db.catalog_count(),
            "status_counts": runtime.db.status_counts(),
            "openai_enabled": runtime.pipeline.cfg.openai_enabled,
            "channel": settings["channel"],
            "profile": {
                "hash": runtime.profile_hash,
            },
        }

    @app.get("/api/profile/status")
    async def profile_status(request: Request):
        runtime = _require_runtime(request)
        return {
            "profile_hash": runtime.profile_hash,
            "email": request.state.account["email"],
        }

    @app.get("/api/recent")
    async def recent(request: Request, limit: int = 50):
        runtime = _require_runtime(request)
        return [r.__dict__ for r in runtime.db.recent_files(limit)]

    # ---- Telegram login -----------------------------------------------------

    @app.post("/api/telegram/set_app_keys")
    async def set_app_keys(body: AppKeysBody, request: Request):
        runtime = _require_runtime(request)
        api_id = body.api_id.strip()
        api_hash = body.api_hash.strip()
        if not api_id.isdigit() or not api_hash:
            raise HTTPException(400, "API ID must be numeric and API Hash must not be empty")
        try:
            await state.get_telegram_client(
                runtime,
                rebuild=True,
                credentials_override={"api_id": api_id, "api_hash": api_hash},
            )
        except Exception as exc:
            raise HTTPException(400, f"Could not connect with these keys: {exc}") from exc
        return {"status": "ok", "note": "Telegram app credentials were validated for this browser profile."}

    @app.post("/api/telegram/send_code")
    async def send_code(body: PhoneBody, request: Request):
        runtime = _require_runtime(request)
        try:
            await (await state.get_telegram_client(runtime, request)).send_code(body.phone)
        except Exception as exc:
            raise HTTPException(400, f"Failed to send code: {exc}") from exc
        runtime.login_phone = body.phone
        return {"status": "code_sent"}

    @app.post("/api/telegram/verify_code")
    async def verify_code(body: CodeBody, request: Request):
        runtime = _require_runtime(request)
        login_phone = runtime.login_phone
        if not login_phone:
            raise HTTPException(400, "Call send_code first")
        try:
            result = await (await state.get_telegram_client(runtime, request)).sign_in_code(login_phone, body.code)
        except Exception as exc:
            raise HTTPException(400, f"Login failed: {exc}") from exc
        return {"status": result}

    @app.post("/api/telegram/verify_password")
    async def verify_password(body: PasswordBody, request: Request):
        runtime = _require_runtime(request)
        try:
            await (await state.get_telegram_client(runtime, request)).sign_in_password(body.password)
        except Exception as exc:
            raise HTTPException(400, f"2FA login failed: {exc}") from exc
        return {"status": "logged_in"}

    @app.post("/api/telegram/logout")
    async def logout(request: Request):
        runtime = _require_runtime(request)
        client = await state.get_telegram_client(runtime, request)
        await client.log_out()
        runtime.login_phone = None
        await state.get_telegram_client(runtime, rebuild=True)
        return {"status": "logged_out"}

    # ---- Catalog / indexing -------------------------------------------------

    @app.get("/api/catalog")
    async def catalog(
        request: Request,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        downloaded_only: bool = False,
        search_any: Optional[str] = None,
        source: Optional[str] = None,
    ):
        runtime = _require_runtime(request)
        search_any_terms = [term.strip() for term in (search_any or "").split(",") if term.strip()]
        items = runtime.db.catalog(
            search=search,
            limit=limit,
            offset=offset,
            downloaded_only=downloaded_only,
            search_any=search_any_terms,
            source=source,
        )

        for item in items:
            if source == "telegram" and item["message_id"] in runtime.telegram_downloading:
                item["status"] = "downloading"
            item["message_id"] = _public_message_id(item["message_id"])
            item["file_exists"] = bool(item.get("status"))

        return {
            "items": items,
            "total": runtime.db.catalog_count(
                search=search,
                downloaded_only=downloaded_only,
                search_any=search_any_terms,
                source=source,
            ),
        }

    async def _run_index(runtime: ProfileRuntime):
        runtime.indexing = True
        runtime.index_progress = 0
        try:
            telegram = runtime.telegram_client
            if telegram is None:
                raise RuntimeError("Telegram client is not available in memory")

            def on_message(message, filename):
                caption = message.message or None
                runtime.db.upsert_catalog_entry(
                    message_id=message.id,
                    filename=filename,
                    caption=caption,
                    size=getattr(message.document, "size", None),
                    message_date=message.date.isoformat() if message.date else None,
                    ext=Path(filename).suffix.lower(),
                    source="telegram",
                )
                runtime.index_progress += 1

            last_message_id = runtime.db.last_indexed_message_id(source="telegram") or 0
            await telegram.index_channel(on_message, min_id=last_message_id)
        except Exception:
            logger.exception("Indexing failed")
        finally:
            runtime.indexing = False

    @app.post("/api/index")
    async def start_index(request: Request):
        runtime = _require_runtime(request)
        telegram = await state.get_telegram_client(runtime, request)
        if not await telegram.is_authorized():
            raise HTTPException(400, "Log in to Telegram first")
        if runtime.indexing:
            return {"status": "already_running"}
        state.spawn(_run_index(runtime))
        return {"status": "started"}

    # ---- On-demand download --------------------------------------------------

    async def _run_download(runtime: ProfileRuntime, profile_name: str, message_id: int):
        try:
            telegram = runtime.telegram_client
            if telegram is None:
                raise RuntimeError("Telegram client is not available in memory")
            message, path, caption = await telegram.download_by_message_id(message_id)
            original_name = path.name
            encrypted_path = _finalize_download(runtime, profile_name, original_name, path)
            runtime.db.mark_downloaded(message.id, str(message.chat_id), original_name, encrypted_path)
        except Exception as exc:
            logger.exception("Download/process failed for message %s", message_id)
            runtime.db.mark_failed(message_id, str(exc))
        finally:
            runtime.telegram_downloading.discard(message_id)

    @app.post("/api/download/{message_id}")
    async def download(message_id: int, request: Request):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        telegram = await state.get_telegram_client(runtime, request)
        downloading = runtime.telegram_downloading
        if not await telegram.is_authorized():
            raise HTTPException(400, "Log in to Telegram first")
        if message_id in downloading:
            return {"status": "already_running"}
        status_now = runtime.db.get_status(message_id)
        if status_now in (
            db_module.STATUS_SORTED,
            db_module.STATUS_SCANNED,
            db_module.STATUS_NEEDS_REVIEW,
        ):
            return {"status": "already_processed", "current_status": status_now}
        downloading.add(message_id)
        state.spawn(_run_download(runtime, profile_name, message_id))
        return {"status": "started"}

    @app.get("/api/arxiv/search")
    async def arxiv_search(request: Request, q: str = "", category: str = "", limit: int = 25, offset: int = 0):
        runtime = _require_runtime(request)
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
            runtime.db.ensure_confirmed_tag("arxiv")
            runtime.db.tag_document(entry.doc_id, "arxiv", auto_tagged=True)
            caption_parts = [entry.summary]
            if entry.authors:
                caption_parts.append(f"Authors: {', '.join(entry.authors)}")
            caption_parts.append(f"arXiv: {entry.arxiv_id}")
            caption = "\n".join(part for part in caption_parts if part)
            runtime.db.upsert_catalog_entry(
                message_id=entry.doc_id,
                filename=entry.filename,
                caption=caption,
                size=None,
                message_date=arxiv_client.message_date_for_catalog(entry.published),
                ext=".pdf",
                source="arxiv",
            )
            record = runtime.db.get_record(entry.doc_id)
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
                "file_exists": _find_existing_file(runtime.pipeline.cfg, record, entry.doc_id) is not None,
            })

        return {"items": items, "total": total}

    async def _run_arxiv_download(
        runtime: ProfileRuntime,
        profile_name: str,
        doc_id: int,
        arxiv_id: str,
        title: str,
        summary: str,
        authors: list[str],
        published: str,
    ):
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
            path = await asyncio.to_thread(arxiv_client.download_pdf, entry, runtime.store.temp_dir)
            encrypted_path = _finalize_download(runtime, profile_name, path.name, path)
            caption_parts = [summary]
            if authors:
                caption_parts.append(f"Authors: {', '.join(authors)}")
            caption_parts.append(f"arXiv: {arxiv_id}")
            runtime.db.upsert_catalog_entry(
                message_id=doc_id,
                filename=entry.filename,
                caption="\n".join(part for part in caption_parts if part),
                size=Path(encrypted_path).stat().st_size if Path(encrypted_path).exists() else None,
                message_date=arxiv_client.message_date_for_catalog(published),
                ext=".pdf",
                source="arxiv",
            )
            runtime.db.ensure_confirmed_tag("arxiv")
            runtime.db.tag_document(doc_id, "arxiv", auto_tagged=True)
            runtime.db.mark_downloaded(doc_id, "arxiv", entry.filename, encrypted_path)
        except Exception as exc:
            logger.exception("Download/process failed for arXiv %s", arxiv_id)
            runtime.db.mark_failed(doc_id, str(exc))
        finally:
            runtime.arxiv_downloading.discard(doc_id)

    @app.post("/api/arxiv/download")
    async def arxiv_download(request: Request, data: dict):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        arxiv_id = str(data.get("arxiv_id", "")).strip()
        title = str(data.get("title", "")).strip()
        summary = str(data.get("summary", "")).strip()
        authors = data.get("authors") or []
        published = str(data.get("published", "")).strip()
        if not arxiv_id or not title:
            raise HTTPException(400, "Missing arXiv document metadata")
        doc_id = int(data.get("message_id") or arxiv_client.stable_doc_id(arxiv_id))
        status_now = runtime.db.get_status(doc_id)
        if status_now in (
            db_module.STATUS_DOWNLOADED,
            db_module.STATUS_SORTED,
            db_module.STATUS_SCANNED,
            db_module.STATUS_NEEDS_REVIEW,
        ):
            return {"status": "already_processed", "current_status": status_now}
        if doc_id in runtime.arxiv_downloading:
            return {"status": "already_running"}
        runtime.arxiv_downloading.add(doc_id)
        state.spawn(_run_arxiv_download(runtime, profile_name, doc_id, arxiv_id, title, summary, authors, published))
        return {"status": "started"}

    @app.post("/api/upload/local")
    async def upload_local_file(request: Request, file: UploadFile = File(...)):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        filename = Path(file.filename or "").name
        if not filename:
            raise HTTPException(400, "Missing filename")

        runtime.store.temp_dir.mkdir(parents=True, exist_ok=True)
        message_id = _next_local_upload_id(runtime.db)
        staging_path = runtime.store.temp_dir / _local_upload_filename(message_id, filename)

        try:
            with staging_path.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
        except Exception as exc:
            raise HTTPException(400, f"Could not save uploaded file: {exc}") from exc
        finally:
            await file.close()

        encrypted_path = _finalize_download(runtime, profile_name, staging_path.name, staging_path)
        size = Path(encrypted_path).stat().st_size if Path(encrypted_path).exists() else None
        runtime.db.upsert_catalog_entry(
            message_id=message_id,
            filename=filename,
            caption="Uploaded from local machine",
            size=size,
            message_date=datetime.now(timezone.utc).isoformat(),
            ext=Path(filename).suffix.lower(),
            source="local",
        )
        runtime.db.mark_downloaded(message_id, "local", filename, encrypted_path)
        return {"status": "uploaded", "message_id": _public_message_id(message_id), "filename": filename}

    @app.post("/api/delete/{message_id}")
    async def delete_file(message_id: int, request: Request):
        runtime = _require_runtime(request)
        record = runtime.db.get_record(message_id)
        if not record:
            raise HTTPException(404, "File not found in database")

        deleted_paths = []
        for path in _candidate_file_locations(runtime.pipeline.cfg, record, message_id):
            if not path.exists():
                continue
            try:
                path.unlink()
                deleted_paths.append(str(path))
            except Exception as exc:
                logger.warning("Failed to delete file %s: %s", path, exc)

        runtime.db.delete_file_record(message_id)

        return {"status": "deleted", "deleted_paths": deleted_paths}

    @app.post("/api/rename/{message_id}")
    async def rename_file(message_id: int, data: dict, request: Request):
        runtime = _require_runtime(request)
        new_filename = data.get("filename", "").strip()
        if not new_filename:
            raise HTTPException(400, "Filename cannot be empty")

        runtime.db.rename_catalog_entry(message_id, new_filename)
        return {"status": "renamed", "filename": new_filename}

    # ---- Tags ----------------------------------------------------------------

    @app.get("/api/tags/extract/{message_id}")
    async def extract_tags_for_document(message_id: int, request: Request):
        runtime = _require_runtime(request)
        with runtime.db._lock:
            cur = runtime.db._conn.execute(
                "SELECT filename, caption FROM catalog WHERE message_id = ?",
                (message_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(404, "Document not found")

        filename = row["filename"]
        caption = row["caption"]

        # Extract tags
        potential_tags = tagger.extract_potential_tags(filename, caption)

        ignored_tags = set()
        with runtime.db._lock:
            cur = runtime.db._conn.execute(
                "SELECT tag FROM suggested_tags WHERE status = 'ignored'"
            )
            ignored_tags = {r["tag"] for r in cur.fetchall()}

        available_tags = [tag for tag in potential_tags if tag not in ignored_tags]
        return {"tags": sorted(available_tags)}

    @app.get("/api/tags/confirmed")
    async def get_confirmed_tags(request: Request):
        runtime = _require_runtime(request)
        return runtime.db.get_confirmed_tags()

    @app.post("/api/tags/confirm/{tag}")
    async def confirm_tag(tag: str, request: Request, message_id: int = None):
        runtime = _require_runtime(request)
        tag = tag.lower().strip()

        with runtime.db._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, status) VALUES (?, 'confirmed')
                ON CONFLICT(tag) DO UPDATE SET status = 'confirmed'
                """,
                (tag,)
            )

        return {"status": "confirmed"}

    @app.post("/api/tags/ignore/{tag}")
    async def ignore_tag(tag: str, request: Request):
        runtime = _require_runtime(request)
        tag = tag.lower().strip()

        with runtime.db._cursor() as cur:
            cur.execute(
                """
                INSERT INTO suggested_tags (tag, status) VALUES (?, 'ignored')
                ON CONFLICT(tag) DO UPDATE SET status = 'ignored'
                """,
                (tag,)
            )
        return {"status": "ignored"}

    @app.get("/api/tags/documents/{tag}")
    async def get_documents_by_tag(tag: str, request: Request):
        runtime = _require_runtime(request)
        return runtime.db.get_documents_by_tag(tag)

    @app.post("/api/catalog/reset")
    async def reset_catalog(request: Request):
        runtime = _require_runtime(request)
        with runtime.db._cursor() as cur:
            cur.execute("DELETE FROM catalog")
            cur.execute("DELETE FROM suggested_tags")
            cur.execute("DELETE FROM document_tags")
        return {"status": "reset"}

    # ---- Background listener ---------------------------------------------

    @app.post("/api/listen/start")
    async def listen_start(request: Request):
        raise HTTPException(
            400,
            "Live listener is not supported for encrypted account storage",
        )

    @app.post("/api/listen/stop")
    async def listen_stop(request: Request):
        runtime = _require_runtime(request)
        task = runtime.listen_task
        runtime.listen_task = None
        if task:
            task.cancel()
        return {"status": "stopped"}

    # ---- Config -------------------------------------------------------------

    @app.get("/api/config")
    async def get_config(request: Request):
        runtime = _require_runtime(request)
        snapshot = state.cfg.as_dict_masked()
        _merge_nested(snapshot, runtime.settings)
        snapshot.setdefault("telegram", {})
        if (runtime.settings.get("telegram") or {}).get("api_hash"):
            snapshot["telegram"]["api_hash"] = "********"
        snapshot["telegram"]["session_name"] = runtime.profile_hash
        snapshot["profile"] = {
            "hash": runtime.profile_hash,
            "storage_dir": str(runtime.store.root_dir),
        }
        return snapshot

    @app.post("/api/config")
    async def save_config(patch: dict, request: Request):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        _strip_masked_secrets(patch, state.cfg)
        telegram_patch = patch.get("telegram") if isinstance(patch.get("telegram"), dict) else None
        if telegram_patch is not None:
            _strip_masked_telegram_secrets(telegram_patch)
            telegram_patch.pop("api_id", None)
            telegram_patch.pop("api_hash", None)
        _merge_nested(runtime.settings, patch)
        state.save_settings(runtime, profile_name)
        if telegram_patch is not None and runtime.telegram_client is not None:
            await state.get_telegram_client(runtime, rebuild=True)
        return {
            "status": "saved",
            "note": "Profile settings were saved encrypted at rest.",
        }

    # ---- Reading progress -----------------------------------------------------

    @app.get("/api/workspace-state")
    async def get_workspace_state(request: Request):
        runtime = _require_runtime(request)
        payload = runtime.settings.get("workspace") or {}
        if not payload:
            return {"open_documents": [], "active_document_id": None}
        return {
            "open_documents": payload.get("open_documents") or [],
            "active_document_id": payload.get("active_document_id"),
        }

    @app.post("/api/workspace-state")
    async def save_workspace_state(
        body: WorkspaceStateBody,
        request: Request,
    ):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        runtime.settings["workspace"] = {
            "open_documents": body.open_documents,
            "active_document_id": body.active_document_id,
        }
        state.save_settings(runtime, profile_name)
        return {"status": "saved"}

    @app.get("/api/document-state")
    async def get_document_state(
        request: Request,
        document_id: str,
        document_kind: str = "file",
    ):
        runtime = _require_runtime(request)
        payload = _document_state_payload(runtime, document_id)
        if payload:
            return {
                "document_id": payload.get("document_id", document_id),
                "document_kind": payload.get("document_kind", document_kind),
                "current_page": payload.get("current_page", 1),
                "total_pages": payload.get("total_pages"),
                "scroll_position": payload.get("scroll_position", 0),
                "viewer_prefs": payload.get("viewer_prefs") or {},
            }
        return {
            "document_id": document_id,
            "document_kind": document_kind,
            "current_page": 1,
            "total_pages": None,
            "scroll_position": 0,
            "viewer_prefs": {},
        }

    @app.post("/api/document-state")
    async def save_document_state(
        body: DocumentStateBody,
        request: Request,
    ):
        runtime = _require_runtime(request)
        profile_name = _profile_name_from_request(request)
        documents = _settings_section(runtime, "documents")
        documents[body.document_id] = {
            "document_id": body.document_id,
            "document_kind": body.document_kind,
            "current_page": body.current_page,
            "total_pages": body.total_pages,
            "scroll_position": str(body.scroll_position),
            "viewer_prefs": body.viewer_prefs,
        }
        state.save_settings(runtime, profile_name)
        return {"status": "saved", "synced": True}

    @app.get("/api/file_status/{message_id}")
    async def file_status(message_id: int, request: Request):
        runtime = _require_runtime(request)
        record = runtime.db.get_record(message_id)
        catalog_entry = runtime.db.get_catalog_entry(message_id)
        path_obj = _find_existing_file(runtime.pipeline.cfg, record, message_id)
        actual_ext = path_obj.suffix.lower() if path_obj and path_obj.is_file() else None
        return {
            "exists": path_obj is not None,
            "filename": catalog_entry.get("filename") if catalog_entry else None,
            "ext": actual_ext or (catalog_entry.get("ext") if catalog_entry else None),
        }

    @app.get("/api/website_title")
    async def website_title(request: Request, url: str):
        _require_runtime(request)
        title = await asyncio.to_thread(_fetch_website_title, url)
        return {"title": title}

    @app.post("/api/ai/ask")
    async def ask_ai(body: AiAskBody, request: Request):
        runtime = _require_runtime(request)
        if not runtime.pipeline.cfg.openai_enabled:
            raise HTTPException(400, "OpenAI is disabled in configuration")

        api_key = str(runtime.pipeline.cfg.get("openai", "api_key", default="")).strip()
        if not api_key or api_key == "YOUR_OPENAI_API_KEY":
            raise HTTPException(400, "OpenAI API key is not configured")

        selection_text = body.selection_text.strip()
        if not selection_text:
            raise HTTPException(400, "No selected text provided")

        action = (body.action or "explain_simple").strip()
        question = (body.question or "").strip()
        model = str(runtime.pipeline.cfg.get("openai", "model", default="gpt-4o-mini")).strip() or "gpt-4o-mini"

        prompts = {
            "explain_simple": "Explain the selected text in simple words for a non-expert reader.",
            "more_examples": "Explain the selected text with a few concrete examples or analogies.",
            "find_references": "Suggest a few relevant related references or literature leads based on the selected text. If you are uncertain, say they are possible leads rather than exact matches.",
            "summarize": "Summarize the selected text concisely.",
        }
        instruction = prompts.get(action, prompts["explain_simple"])
        if action == "custom":
            if not question:
                raise HTTPException(400, "Custom question is empty")
            instruction = question

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise HTTPException(500, "openai package is not installed") from exc

        client = OpenAI(api_key=api_key)
        clipped_text = selection_text[:6000]
        source = (body.source or "").strip()
        source_line = f"Source type: {source}\n" if source else ""

        try:
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=model,
                    temperature=0.3,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You answer questions about selected reading text. "
                                "Be accurate, concise, and directly useful. "
                                "If the text is ambiguous, say so plainly."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"{source_line}"
                                f"Instruction: {instruction}\n\n"
                                f"Selected text:\n\"\"\"\n{clipped_text}\n\"\"\""
                            ),
                        },
                    ],
                )
            )
        except Exception as exc:
            logger.exception("AI request failed")
            raise HTTPException(502, f"AI request failed: {exc}") from exc

        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            raise HTTPException(502, "AI returned an empty response")
        return {"answer": answer}

    # ---- File serving ---------------------------------------------------------

    @app.options("/api/file/{message_id}")
    async def serve_file_options(message_id: int):
        from fastapi.responses import Response
        response = Response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Range, Content-Type, Authorization"
        )
        response.headers["Access-Control-Max-Age"] = "86400"
        return response

    @app.get("/api/file-manifest/{message_id}")
    async def file_manifest(message_id: int, request: Request):
        runtime = _runtime_for_chunk_request(request)
        record = runtime.db.get_record(message_id)
        path_obj = _find_existing_file(runtime.pipeline.cfg, record, message_id)
        if not path_obj or not path_obj.exists():
            raise HTTPException(404, f"File not found for message_id={message_id}")
        manifest = runtime.store.load_file_manifest(path_obj)
        return {
            "message_id": str(message_id),
            "profile_hash": runtime.profile_hash,
            **manifest,
        }

    @app.get("/api/file-chunk/{message_id}/{chunk_index}")
    async def file_chunk(message_id: int, chunk_index: int, request: Request, decrypt: bool = False):
        runtime = _runtime_for_chunk_request(request)
        record = runtime.db.get_record(message_id)
        path_obj = _find_existing_file(runtime.pipeline.cfg, record, message_id)
        if not path_obj or not path_obj.exists():
            raise HTTPException(404, f"File not found for message_id={message_id}")
        if decrypt:
            profile_name = _profile_name_from_request(request)
            payload = runtime.store.decrypt_file_chunk(profile_name, path_obj, chunk_index)
            media_type = "application/octet-stream"
        else:
            payload = runtime.store.read_encrypted_file_chunk(path_obj, chunk_index)
            media_type = "application/octet-stream"
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "Access-Control-Allow-Origin": "*",
            },
        )

    def _build_file_response(runtime: ProfileRuntime, message_id: int, request: Request, head_only: bool = False):
        import mimetypes

        record = runtime.db.get_record(message_id)
        path_obj = _find_existing_file(runtime.pipeline.cfg, record, message_id)

        if not path_obj or not path_obj.exists():
            raise HTTPException(404, f"File not found for message_id={message_id}")

        catalog_entry = runtime.db.get_catalog_entry(message_id) or {}
        filename = str(catalog_entry.get("filename") or path_obj.stem)
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            ext = Path(filename).suffix.lower()
            mime_types_map = {
                '.pdf': 'application/pdf',
                '.epub': 'application/epub+zip',
                '.cbz': 'application/x-cbz',
                '.cbr': 'application/x-cbr',
            }
            mime_type = mime_types_map.get(ext, 'application/octet-stream')

        try:
            manifest = runtime.store.load_file_manifest(path_obj)
        except ProfileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"Cannot read file: {exc}") from exc

        file_size = int(manifest.get("plaintext_size") or 0)

        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Authorization",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
        }

        byte_range = _parse_range_header(request.headers.get("range"), file_size)
        if byte_range is None:
            headers["Content-Length"] = str(file_size)
            if head_only:
                return Response(status_code=200, media_type=mime_type, headers=headers)
            def iter_full_file():
                profile_name = _profile_name_from_request(request)
                chunk_count = int(manifest.get("chunk_count") or 0)
                for chunk_index in range(chunk_count):
                    yield runtime.store.decrypt_file_chunk(profile_name, path_obj, chunk_index, manifest=manifest)

            return StreamingResponse(iter_full_file(), status_code=200, media_type=mime_type, headers=headers)

        start, end = byte_range
        chunk_size = end - start + 1
        headers["Content-Length"] = str(chunk_size)
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

        if head_only:
            return Response(status_code=206, media_type=mime_type, headers=headers)

        def iter_file():
            profile_name = _profile_name_from_request(request)
            storage_chunk_size = int(manifest.get("chunk_size") or state.cfg.encryption_chunk_size)
            start_chunk = start // storage_chunk_size
            end_chunk = end // storage_chunk_size
            for chunk_index in range(start_chunk, end_chunk + 1):
                chunk = runtime.store.decrypt_file_chunk(profile_name, path_obj, chunk_index, manifest=manifest)
                chunk_start = chunk_index * storage_chunk_size
                slice_start = max(0, start - chunk_start)
                slice_end = min(len(chunk), end - chunk_start + 1)
                if slice_start < slice_end:
                    yield chunk[slice_start:slice_end]

        return StreamingResponse(iter_file(), status_code=206, media_type=mime_type, headers=headers)

    @app.head("/api/file/{message_id}")
    async def serve_file_head(message_id: int, request: Request):
        runtime = _require_runtime(request)
        return _build_file_response(runtime, message_id, request, head_only=True)

    @app.get("/api/file/{message_id}")
    async def serve_file(message_id: int, request: Request):
        runtime = _require_runtime(request)
        return _build_file_response(runtime, message_id, request)

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


def _strip_masked_telegram_secrets(patch: dict) -> None:
    if patch.get("api_hash") == "********":
        del patch["api_hash"]
