"""Optional LLM-based content classification via OpenAI.

Determines content_type (book/magazine/comic-or-manga/academic-article) and
category/genre, with a confidence score. Disabled entirely if openai.enabled
is false in config, in which case callers should fall back to filename/metadata
heuristics only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from .metadata import Metadata

logger = logging.getLogger("estratto.classifier")

VALID_CONTENT_TYPES = ("book", "magazine", "comic-or-manga", "academic-article")

_SYSTEM_PROMPT = (
    "You are a librarian assistant classifying files for a personal digital library. "
    "Given a filename, any extracted metadata, a short text excerpt, and an optional caption, "
    "determine the content type and category/genre. "
    "Respond only with the structured fields requested."
)

_FUNCTION_SCHEMA = {
    "name": "classify_content",
    "description": "Classify a library file's content type and category.",
    "parameters": {
        "type": "object",
        "properties": {
            "content_type": {
                "type": "string",
                "enum": list(VALID_CONTENT_TYPES),
                "description": "Top-level library the file belongs to.",
            },
            "category": {
                "type": "string",
                "description": "Genre/category, e.g. Fiction, Non-Fiction, Cooking, Photography, Science, History, Technology.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in this classification, from 0.0 to 1.0.",
            },
        },
        "required": ["content_type", "category", "confidence"],
    },
}


@dataclass
class Classification:
    content_type: str
    category: str
    confidence: float


def _build_prompt(filename: str, meta: Metadata, caption: Optional[str], excerpt_chars: int) -> str:
    parts = [f"Filename: {filename}"]
    if meta.title:
        parts.append(f"Title: {meta.title}")
    if meta.author:
        parts.append(f"Author: {meta.author}")
    if meta.series:
        parts.append(f"Series: {meta.series}")
    if meta.magazine:
        parts.append(f"Magazine: {meta.magazine}")
    if caption:
        parts.append(f"Telegram caption: {caption}")
    excerpt = (meta.excerpt or "")[:excerpt_chars]
    if excerpt:
        parts.append(f"Text excerpt:\n{excerpt}")
    return "\n".join(parts)


def classify(
    filename: str,
    meta: Metadata,
    caption: Optional[str],
    api_key: str,
    model: str,
    excerpt_chars: int = 1500,
) -> Optional[Classification]:
    """Call OpenAI to classify content. Returns None on any failure (caller should
    treat that like low confidence / route to needs-review)."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed; cannot classify. Run: pip install openai")
        return None

    client = OpenAI(api_key=api_key)
    user_prompt = _build_prompt(filename, meta, caption, excerpt_chars)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tools=[{"type": "function", "function": _FUNCTION_SCHEMA}],
            tool_choice={"type": "function", "function": {"name": "classify_content"}},
            temperature=0,
        )
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            logger.warning("OpenAI classification returned no tool call for %s", filename)
            return None
        args = json.loads(tool_calls[0].function.arguments)
        content_type = args.get("content_type")
        category = args.get("category")
        confidence = float(args.get("confidence", 0.0))
        if content_type not in VALID_CONTENT_TYPES or not category:
            logger.warning("OpenAI classification returned invalid data for %s: %r", filename, args)
            return None
        return Classification(content_type=content_type, category=category, confidence=confidence)
    except Exception as exc:
        logger.error("OpenAI classification failed for %s: %s", filename, exc)
        return None
