"""Config loading for Estratto: reads config.yaml, applies environment overrides, and
supports round-trip saving (preserving comments/formatting) for the web UI's config editor."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .paths import default_config_path

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=2, offset=0)

# Secrets that can be set via env var instead of config.yaml. These are applied as a
# read-only overlay at get()-time and are never written back by save().
_ENV_OVERRIDES = {
    ("telegram", "api_id"): "ESTRATTO_TELEGRAM_API_ID",
    ("telegram", "api_hash"): "ESTRATTO_TELEGRAM_API_HASH",
    ("openai", "api_key"): "ESTRATTO_OPENAI_API_KEY",
    ("kavita", "api_key"): "ESTRATTO_KAVITA_API_KEY",
    ("kavita", "base_url"): "ESTRATTO_KAVITA_BASE_URL",
}

# Keys treated as secret: masked when serialized for the web UI's config viewer.
SECRET_KEYS = {
    ("telegram", "api_hash"),
    ("openai", "api_key"),
    ("kavita", "api_key"),
}


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path = field(default_factory=default_config_path)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path).expanduser() if path else default_config_path()
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}. Copy config.yaml and fill in your values."
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = _yaml.load(f) or {}
        return cls(raw=raw, path=path)

    def save(self) -> None:
        """Write the current config back to disk, preserving comments/formatting where possible."""
        with open(self.path, "w", encoding="utf-8") as f:
            _yaml.dump(self.raw, f)

    def get(self, *keys: str, default: Any = None) -> Any:
        if keys in _ENV_OVERRIDES and _ENV_OVERRIDES[keys] in os.environ:
            return os.environ[_ENV_OVERRIDES[keys]]
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys_and_value: Any) -> None:
        """set('telegram', 'channel', 'foo') sets raw['telegram']['channel'] = 'foo'."""
        *keys, value = keys_and_value
        node = self.raw
        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        node[keys[-1]] = value

    def update(self, patch: dict[str, Any]) -> None:
        """Deep-merge a nested dict (e.g. parsed from a web form/JSON body) into raw config."""
        def _merge(dst: dict, src: dict) -> None:
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    _merge(dst[k], v)
                else:
                    dst[k] = v

        _merge(self.raw, patch)

    def as_dict_masked(self) -> dict[str, Any]:
        """Plain dict snapshot with secret fields masked, safe to send to the browser."""
        import copy

        snapshot = copy.deepcopy(dict(self.raw))

        def _mask(node: dict, path: tuple[str, ...]) -> None:
            for k, v in list(node.items()):
                key_path = path + (k,)
                if isinstance(v, dict):
                    _mask(v, key_path)
                elif key_path in SECRET_KEYS and v:
                    node[k] = "********" if len(str(v)) > 0 else v

        _mask(snapshot, ())
        return snapshot

    def resolve_path(self, raw_path: str | Path | None, default: str = "") -> Path:
        candidate = raw_path if raw_path not in (None, "") else default
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = (self.path.parent / path).resolve()
        return path

    # Convenience accessors -------------------------------------------------

    @property
    def staging_dir(self) -> Path:
        return self.resolve_path(self.get("staging", "dir", default="./staging"))

    @property
    def needs_review_dir(self) -> Path:
        return self.resolve_path(self.get("staging", "needs_review_dir", default="./needs-review"))

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.get("db", "path", default="./estratto.db"))

    @property
    def telegram_session_name(self) -> str:
        return str(self.resolve_path(self.get("telegram", "session_name", default="estratto")))

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.lower() for e in self.get("extensions", default=[])}

    @property
    def openai_enabled(self) -> bool:
        return bool(self.get("openai", "enabled", default=False))

    @property
    def web_host(self) -> str:
        return str(self.get("web", "host", default="0.0.0.0"))

    @property
    def web_port(self) -> int:
        return int(self.get("web", "port", default=8000))
