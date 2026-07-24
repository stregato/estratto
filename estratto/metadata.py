"""Metadata extraction: EPUB/PDF embedded metadata, filename parsing fallback, text excerpts."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("estratto.metadata")

ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class Metadata:
    title: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    magazine: Optional[str] = None
    year: Optional[str] = None
    issue: Optional[str] = None
    volume: Optional[str] = None
    source: str = "unknown"  # "embedded" | "filename" | "caption"
    excerpt: str = ""
    fields_from: dict = field(default_factory=dict)  # field name -> source, for logging


def sanitize(value: Optional[str]) -> Optional[str]:
    """Strip illegal filesystem characters, collapse whitespace, trim."""
    if not value:
        return value
    value = ILLEGAL_CHARS_RE.sub("", value)
    value = WHITESPACE_RE.sub(" ", value).strip()
    return value or None


def title_case(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    # Avoid mangling all-caps acronyms; only title-case words that are all lowercase or all uppercase-and-long.
    words = value.split(" ")
    out = []
    for w in words:
        if w.isupper() and len(w) <= 4:
            out.append(w)  # keep short acronyms as-is
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)


def normalize(meta: Metadata) -> Metadata:
    meta.title = title_case(sanitize(meta.title))
    meta.author = title_case(sanitize(meta.author))
    meta.series = title_case(sanitize(meta.series))
    meta.magazine = title_case(sanitize(meta.magazine))
    meta.year = sanitize(meta.year)
    meta.issue = sanitize(meta.issue)
    meta.volume = sanitize(meta.volume)
    return meta


def extract_epub_metadata(path: Path) -> Metadata:
    """Pull title/author/series from EPUB OPF metadata via ebooklib."""
    meta = Metadata(source="embedded")
    try:
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(str(path), options={"ignore_ncx": True})

        def _first(values):
            return values[0][0] if values else None

        meta.title = _first(book.get_metadata("DC", "title"))
        meta.author = _first(book.get_metadata("DC", "creator"))

        # Series is non-standard; check common Calibre meta tags.
        for meta_tag in book.get_metadata("OPF", "meta"):
            attrs = meta_tag[1] if len(meta_tag) > 1 else {}
            name = attrs.get("name", "")
            if name == "calibre:series":
                meta.series = attrs.get("content")

        # Grab a short text excerpt from the first document item for LLM classification.
        excerpt_parts = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                try:
                    from bs4 import BeautifulSoup

                    text = BeautifulSoup(item.get_content(), "html.parser").get_text(" ", strip=True)
                except Exception:
                    text = item.get_content().decode("utf-8", errors="ignore")
                if text:
                    excerpt_parts.append(text)
                if sum(len(p) for p in excerpt_parts) > 2000:
                    break
        meta.excerpt = " ".join(excerpt_parts)[:2000]
    except Exception as exc:
        logger.warning("EPUB metadata extraction failed for %s: %s", path, exc)
    return meta


def extract_pdf_metadata(path: Path) -> Metadata:
    """Try embedded PDF metadata (pypdf, then PyMuPDF), plus a text excerpt."""
    meta = Metadata(source="embedded")

    # Try pypdf first.
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        info = reader.metadata or {}
        meta.title = getattr(info, "title", None) or info.get("/Title") if info else None
        meta.author = getattr(info, "author", None) or info.get("/Author") if info else None

        excerpt_parts = []
        for page in reader.pages[:2]:
            text = page.extract_text() or ""
            excerpt_parts.append(text)
            if sum(len(p) for p in excerpt_parts) > 2000:
                break
        meta.excerpt = " ".join(excerpt_parts)[:2000]
    except Exception as exc:
        logger.debug("pypdf extraction failed for %s: %s", path, exc)

    # Fall back to / augment with PyMuPDF if pypdf didn't get title/author.
    if not meta.title or not meta.author:
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(path))
            info = doc.metadata or {}
            meta.title = meta.title or info.get("title") or None
            meta.author = meta.author or info.get("author") or None
            if not meta.excerpt:
                excerpt_parts = []
                for page in doc[:2]:
                    excerpt_parts.append(page.get_text())
                meta.excerpt = " ".join(excerpt_parts)[:2000]
            doc.close()
        except Exception as exc:
            logger.debug("PyMuPDF extraction failed for %s: %s", path, exc)

    # Treat obviously junk/empty values as unreliable.
    if meta.title and len(meta.title.strip()) < 2:
        meta.title = None
    if meta.author and len(meta.author.strip()) < 2:
        meta.author = None

    return meta


def parse_filename(filename: str, patterns: list[str]) -> Metadata:
    """Apply configured regex patterns in order; first match wins."""
    stem = Path(filename).stem
    meta = Metadata(source="filename")
    for pattern in patterns:
        try:
            m = re.match(pattern, stem)
        except re.error as exc:
            logger.warning("Invalid filename pattern %r: %s", pattern, exc)
            continue
        if m:
            groups = m.groupdict()
            for field_name in ("title", "author", "series", "magazine", "year", "issue", "volume"):
                if field_name in groups and groups[field_name]:
                    setattr(meta, field_name, groups[field_name])
            return meta
    # No pattern matched: use whole stem as title, nothing else.
    meta.title = stem
    return meta


def merge(primary: Metadata, fallback: Metadata) -> Metadata:
    """Fill any missing fields in `primary` from `fallback`; track provenance."""
    merged = Metadata(source=primary.source, excerpt=primary.excerpt or fallback.excerpt)
    for field_name in ("title", "author", "series", "magazine", "year", "issue", "volume"):
        primary_val = getattr(primary, field_name)
        if primary_val:
            setattr(merged, field_name, primary_val)
            merged.fields_from[field_name] = primary.source
        else:
            fallback_val = getattr(fallback, field_name)
            setattr(merged, field_name, fallback_val)
            if fallback_val:
                merged.fields_from[field_name] = fallback.source
    return merged


def extract(path: Path, caption: Optional[str], filename_patterns: list[str]) -> Metadata:
    """Top-level extraction: embedded metadata first, filename/caption as fallback."""
    ext = path.suffix.lower()
    if ext == ".epub":
        embedded = extract_epub_metadata(path)
    elif ext == ".pdf":
        embedded = extract_pdf_metadata(path)
    else:
        # .cbz/.cbr/.mobi: filename parsing only per spec.
        embedded = Metadata(source="embedded")

    filename_meta = parse_filename(path.name, filename_patterns)
    if caption and not filename_meta.title:
        caption_meta = parse_filename(caption, filename_patterns)
        caption_meta.source = "caption"
        filename_meta = merge(filename_meta, caption_meta)

    merged = merge(embedded, filename_meta)
    return normalize(merged)
