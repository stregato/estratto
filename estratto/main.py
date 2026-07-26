"""CLI entrypoint: wires Telegram ingestion and local file storage together."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from telethon.tl.types import Message

from . import db as db_module
from .config import Config
from .kavita_client import KavitaClient
from .telegram_client import EstrattoTelegramClient

logger = logging.getLogger("estratto")


def setup_logging(cfg: Config) -> None:
    level_name = cfg.get("logging", "level", default="INFO")
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_file = cfg.get("logging", "file", default="")
    if log_file:
        log_path = cfg.resolve_path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


class Pipeline:
    """Holds shared state for processing downloaded files into local storage."""

    def __init__(self, cfg: Config, database: db_module.Database, kavita: Optional[KavitaClient]):
        self.cfg = cfg
        self.db = database
        self.kavita = kavita

    async def process_file(self, message: Message, staging_path, caption: Optional[str]) -> None:
        message_id = message.id
        self.db.mark_downloaded(message_id, str(message.chat_id), staging_path.name, str(staging_path))
        logger.info("Stored %s locally at %s", staging_path.name, staging_path)

    def is_processed(self, message_id: int) -> bool:
        return self.db.is_processed(message_id)

    async def flush_due_scans(self, debounce_seconds: int) -> None:
        return

    async def scan_debounce_loop(self, debounce_seconds: int, poll_interval: int = 10) -> None:
        while True:
            await asyncio.sleep(poll_interval)
            try:
                await self.flush_due_scans(debounce_seconds)
            except Exception:
                logger.exception("Error flushing debounced scans")


def _build_kavita_client(cfg: Config) -> Optional[KavitaClient]:
    return None


async def _run(cfg: Config, mode: str) -> None:
    database = db_module.Database(cfg.db_path)
    kavita = _build_kavita_client(cfg)
    pipeline = Pipeline(cfg, database, kavita)

    telegram = EstrattoTelegramClient(
        api_id=int(cfg.get("telegram", "api_id")),
        api_hash=str(cfg.get("telegram", "api_hash")),
        session_name=cfg.telegram_session_name,
        channel=str(cfg.get("telegram", "channel")),
        staging_dir=cfg.staging_dir,
        allowed_extensions=cfg.allowed_extensions,
    )

    await telegram.start()
    try:
        if mode == "backfill":
            await telegram.backfill(pipeline.process_file, pipeline.is_processed)
        elif mode == "listen":
            await telegram.listen(pipeline.process_file, pipeline.is_processed)
        else:
            raise ValueError(f"Unknown mode: {mode}")
    finally:
        await telegram.stop()
        database.close()


def _run_web(config_path: str) -> None:
    import uvicorn

    from .webapp import create_app

    cfg = Config.load(config_path)
    setup_logging(cfg)
    app = create_app(config_path)
    uvicorn.run(app, host=cfg.web_host, port=cfg.web_port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(prog="estratto", description="Telegram document fetcher and local library browser")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("backfill", help="Process a channel's full message history once")
    subparsers.add_parser("listen", help="Run continuously, reacting to new messages")
    subparsers.add_parser("web", help="Run the web UI (browse/select files, edit config, Telegram login)")
    subparsers.add_parser("desktop", help="Run the local desktop launcher")

    args = parser.parse_args()

    if args.command == "desktop":
        from .desktop import main as run_desktop

        run_desktop()
        return

    if args.command == "web":
        _run_web(args.config)
        return

    cfg = Config.load(args.config)
    setup_logging(cfg)

    try:
        asyncio.run(_run(cfg, args.command))
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")


if __name__ == "__main__":
    main()
