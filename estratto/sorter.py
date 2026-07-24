"""Path resolution and file moves into the Kavita library layout."""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .metadata import Metadata, sanitize

logger = logging.getLogger("estratto.sorter")

# Classifier content types -> library config keys used in config.yaml `libraries`/`kavita.library_ids`.
CONTENT_TYPE_TO_LIBRARY_KEY = {
    "book": "book",
    "magazine": "magazine",
    "comic-or-manga": "comic",
    "academic-article": "academic-article",
}


class SortError(Exception):
    """Raised when a file cannot be confidently sorted; caller should route to needs-review."""


@dataclass
class SortResult:
    final_path: Path
    library_key: str
    fields_used: dict  # field -> value actually used in the path


def _safe_component(value: Optional[str], fallback: str = "Unknown") -> str:
    cleaned = sanitize(value)
    return cleaned if cleaned else fallback


def resolve_path(
    library_key: str,
    ext: str,
    meta: Metadata,
    category: str,
    libraries_config: dict,
) -> tuple[Path, dict]:
    """Fill the configured template for this library type with sanitized metadata fields."""
    lib_cfg = libraries_config.get(library_key)
    if not lib_cfg:
        raise SortError(f"No library configuration for content type '{library_key}'")

    root = Path(lib_cfg["root"])
    template = lib_cfg["template"]

    values = {
        "category": _safe_component(category),
        "author": _safe_component(meta.author),
        "title": _safe_component(meta.title),
        "magazine": _safe_component(meta.magazine or meta.title),
        "year": _safe_component(meta.year, fallback="Unknown Year"),
        "issue": _safe_component(meta.issue, fallback=""),
        "series": _safe_component(meta.series or meta.title),
        "volume": _safe_component(meta.volume, fallback=""),
        "ext": ext.lstrip("."),
    }

    # Required fields per template type — if missing, this file isn't confidently sortable.
    required_by_key = {
        "book": ["author", "title"],
        "magazine": ["magazine", "year"],
        "comic": ["series"],
        "academic-article": ["author", "title"],
    }
    for field_name in required_by_key.get(library_key, []):
        if values.get(field_name) in (None, "", "Unknown", "Unknown Year"):
            raise SortError(
                f"Missing required field '{field_name}' for content type '{library_key}'"
            )

    try:
        relative = template.format(**values)
    except KeyError as exc:
        raise SortError(f"Template for '{library_key}' references unknown field {exc}") from exc

    # Collapse doubled path separators from empty {issue}/{volume} placeholders.
    relative = relative.replace("  ", " ").strip()
    final_path = root / relative
    return final_path, values


def move_into_library(
    staging_path: Path,
    library_key: str,
    meta: Metadata,
    category: str,
    libraries_config: dict,
) -> SortResult:
    """Resolve the final path and move (not copy) the file there."""
    ext = staging_path.suffix
    final_path, values = resolve_path(library_key, ext, meta, category, libraries_config)

    final_path.parent.mkdir(parents=True, exist_ok=True)
    if final_path.exists():
        logger.warning("Destination already exists, will overwrite: %s", final_path)

    shutil.move(str(staging_path), str(final_path))

    logger.info(
        "Sorted %s -> %s (library=%s, fields_from=%s)",
        staging_path.name,
        final_path,
        library_key,
        meta.fields_from,
    )

    return SortResult(final_path=final_path, library_key=library_key, fields_used=values)
