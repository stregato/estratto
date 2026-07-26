"""Telethon-based ingestion: one-time backfill and long-running listener modes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeFilename, Message

logger = logging.getLogger("estratto.telegram")

MIME_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "application/x-mobipocket-ebook": ".mobi",
    "application/vnd.amazon.ebook": ".azw",
    "application/x-cbz": ".cbz",
    "application/x-cbr": ".cbr",
    "application/zip": ".cbz",
    "application/x-rar-compressed": ".cbr",
}


def matched_filename(message: Message, allowed_extensions: set[str]) -> Optional[str]:
    """Return the document filename if this message has a document with an allowed extension."""
    if not message.document:
        return None
    filename = None
    for attr in message.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            filename = attr.file_name
            break

    if filename:
        ext = Path(filename).suffix.lower()
    else:
        mime_type = (getattr(message.document, "mime_type", "") or "").lower()
        ext = MIME_TYPE_EXTENSIONS.get(mime_type, "")
        if not ext:
            return None
        filename = f"document_{message.id}{ext}"

    if ext not in allowed_extensions:
        return None
    return filename


class EstrattoTelegramClient:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str,
        channel: str,
        staging_dir: Path,
        allowed_extensions: set[str],
    ):
        session_path = Path(session_name).expanduser()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(session_name, api_id, api_hash)
        self.channel = channel
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_extensions = allowed_extensions

    async def start(self) -> None:
        """Connect and prompt for login on first run (phone/code/2FA via stdin). CLI use only."""
        await self.client.start()
        logger.info("Connected to Telegram")

    async def stop(self) -> None:
        await self.client.disconnect()

    # Non-interactive connect + login flow, for driving auth from the web UI ----

    async def connect(self) -> None:
        """Connect the socket without triggering Telethon's interactive stdin login prompt."""
        if not self.client.is_connected():
            await self.client.connect()

    async def is_authorized(self) -> bool:
        await self.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str) -> None:
        await self.connect()
        await self.client.send_code_request(phone)

    async def sign_in_code(self, phone: str, code: str) -> str:
        """Returns 'logged_in' or 'password_required' (2FA enabled)."""
        await self.connect()
        try:
            await self.client.sign_in(phone=phone, code=code)
            return "logged_in"
        except SessionPasswordNeededError:
            return "password_required"

    async def sign_in_password(self, password: str) -> None:
        await self.connect()
        await self.client.sign_in(password=password)

    async def log_out(self) -> None:
        await self.connect()
        await self.client.log_out()

    def _staging_path(self, message_id: int, filename: str) -> Path:
        # Preserve original filename plus message ID to avoid collisions and allow idempotent re-runs.
        stem = Path(filename).stem
        ext = Path(filename).suffix
        return self.staging_dir / f"{stem}__msg{message_id}{ext}"

    async def _download_message(self, message: Message, filename: str) -> Path:
        dest = self._staging_path(message.id, filename)
        if dest.exists():
            logger.info("Already staged, skipping download: %s", dest)
            return dest
        logger.info("Downloading message %s -> %s", message.id, dest)
        await self.client.download_media(message, file=str(dest))
        return dest

    async def backfill(
        self,
        on_file: Callable[[Message, Path, Optional[str]], "any"],
        is_processed: Callable[[int], bool],
    ) -> None:
        """Iterate the channel's full message history once."""
        entity = await self.client.get_entity(self.channel)
        count = 0
        async for message in self.client.iter_messages(entity, reverse=True):
            filename = matched_filename(message, self.allowed_extensions)
            if not filename:
                continue
            if is_processed(message.id):
                logger.debug("Message %s already processed, skipping", message.id)
                continue
            path = await self._download_message(message, filename)
            caption = message.message or None
            result = on_file(message, path, caption)
            if hasattr(result, "__await__"):
                await result
            count += 1
        logger.info("Backfill complete: %d files processed", count)

    async def index_channel(
        self,
        on_message: Callable[[Message, str], "any"],
        min_id: int = 0,
    ) -> int:
        """List matching documents in the channel without downloading them, for the web catalog.

        Calls on_message(message, filename) for every matching document with id > min_id.
        Iterates newest-first so recent documents appear immediately in the catalog.
        """
        entity = await self.client.get_entity(self.channel)
        count = 0
        async for message in self.client.iter_messages(entity, min_id=min_id):
            filename = matched_filename(message, self.allowed_extensions)
            if not filename:
                continue
            result = on_message(message, filename)
            if hasattr(result, "__await__"):
                await result
            count += 1
        logger.info("Indexed %d new files", count)
        return count

    async def download_by_message_id(
        self,
        message_id: int,
    ) -> tuple[Message, Path, Optional[str]]:
        """Fetch and download a single message by ID, for on-demand downloads from the web UI."""
        entity = await self.client.get_entity(self.channel)
        message = await self.client.get_messages(entity, ids=message_id)
        if message is None:
            raise ValueError(f"Message {message_id} not found in {self.channel}")
        filename = matched_filename(message, self.allowed_extensions)
        if not filename:
            raise ValueError(f"Message {message_id} has no matching document")
        path = await self._download_message(message, filename)
        caption = message.message or None
        return message, path, caption

    async def listen(
        self,
        on_file: Callable[[Message, Path, Optional[str]], "any"],
        is_processed: Callable[[int], bool],
    ) -> None:
        """React to new messages as they arrive. Blocks until disconnected."""
        entity = await self.client.get_entity(self.channel)

        @self.client.on(events.NewMessage(chats=entity))
        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            filename = matched_filename(message, self.allowed_extensions)
            if not filename:
                return
            if is_processed(message.id):
                return
            path = await self._download_message(message, filename)
            caption = message.message or None
            result = on_file(message, path, caption)
            if hasattr(result, "__await__"):
                await result

        logger.info("Listening for new messages on %s", self.channel)
        await self.client.run_until_disconnected()
