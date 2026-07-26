from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .config import Config
from .main import setup_logging
from .paths import ensure_runtime_config
from .webapp import create_app


def _open_browser_when_ready(url: str) -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{url}/api/status", timeout=1):
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.5)


def main() -> None:
    config_path = ensure_runtime_config()
    cfg = Config.load(config_path)
    setup_logging(cfg)
    app = create_app(str(config_path))
    url = f"http://127.0.0.1:{cfg.web_port}"
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=cfg.web_port, log_level="info")


if __name__ == "__main__":
    main()
