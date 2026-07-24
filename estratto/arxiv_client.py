"""Minimal arXiv search and PDF download support."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import requests

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "opensearch": "http://a9.com/-/spec/opensearch/1.1/"}


@dataclass
class ArxivEntry:
    doc_id: int
    arxiv_id: str
    title: str
    summary: str
    authors: list[str]
    published: str
    pdf_url: str

    @property
    def filename(self) -> str:
        safe_title = re.sub(r'[\\/:*?"<>|]+', " ", self.title).strip()
        safe_title = re.sub(r"\s+", " ", safe_title)[:140].strip() or self.arxiv_id
        return f"{safe_title} [{self.arxiv_id}].pdf"


def stable_doc_id(arxiv_id: str) -> int:
    digest = hashlib.sha1(arxiv_id.encode("utf-8")).hexdigest()
    return -int(digest[:15], 16)


def _entry_arxiv_id(raw_id: str) -> str:
    return raw_id.rstrip("/").split("/")[-1]


def _build_search_query(query: str, category: str) -> str:
    parts = []
    if query.strip():
        parts.append(f'all:"{query.strip()}"')
    if category.strip():
        parts.append(f"cat:{category.strip()}")
    if not parts:
        raise ValueError("Enter a search term or category")
    return " AND ".join(parts)


def search(query: str, category: str = "", start: int = 0, max_results: int = 50, timeout: int = 20) -> tuple[list[ArxivEntry], int]:
    params = {
        "search_query": _build_search_query(query, category),
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = requests.get(ARXIV_API_URL, params=params, timeout=timeout, headers={"User-Agent": "Estratto/1.0"})
    response.raise_for_status()

    root = ET.fromstring(response.text)
    total_text = root.findtext("opensearch:totalResults", default="0", namespaces=ATOM_NS)
    total = int(total_text or 0)
    entries: list[ArxivEntry] = []

    for node in root.findall("atom:entry", ATOM_NS):
        raw_id = node.findtext("atom:id", default="", namespaces=ATOM_NS)
        arxiv_id = _entry_arxiv_id(raw_id)
        title = " ".join((node.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split())
        summary = " ".join((node.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").split())
        published = node.findtext("atom:published", default="", namespaces=ATOM_NS)
        authors = [
            " ".join((author.findtext("atom:name", default="", namespaces=ATOM_NS) or "").split())
            for author in node.findall("atom:author", ATOM_NS)
        ]
        pdf_url = ""
        for link in node.findall("atom:link", ATOM_NS):
            if link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href", "")
                break
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        if not arxiv_id or not pdf_url:
            continue
        entries.append(
            ArxivEntry(
                doc_id=stable_doc_id(arxiv_id),
                arxiv_id=arxiv_id,
                title=title or arxiv_id,
                summary=summary,
                authors=[name for name in authors if name],
                published=published,
                pdf_url=pdf_url,
            )
        )

    return entries, total


def download_pdf(entry: ArxivEntry, staging_dir: Path, timeout: int = 60) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / entry.filename
    if dest.exists():
        return dest

    with requests.get(entry.pdf_url, timeout=timeout, stream=True, headers={"User-Agent": "Estratto/1.0"}) as response:
        response.raise_for_status()
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
    return dest


def message_date_for_catalog(published: str) -> str:
    if not published:
        return datetime.utcnow().isoformat()
    return published
