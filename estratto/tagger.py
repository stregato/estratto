"""Tag extraction from filenames and captions."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Set

# Separators to split on
SEPARATORS = re.compile(r'[\W_]+', re.UNICODE)  # Any non-letter/non-digit (includes spaces, underscores, etc.)

# Common words to ignore (too generic to be useful tags)
# Includes articles, prepositions, conjunctions, and common words in multiple languages
STOPWORDS = {
    # English
    'the', 'a', 'an', 'and', 'or', 'but', 'nor', 'yet', 'so',
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as',
    'into', 'onto', 'upon', 'after', 'before', 'between', 'among', 'through',
    'during', 'without', 'within', 'about', 'above', 'below', 'under', 'over',
    'is', 'am', 'are', 'was', 'were', 'been', 'be', 'being',
    'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing',
    'will', 'would', 'should', 'could', 'may', 'might', 'must', 'can',
    'this', 'that', 'these', 'those', 'such',
    'i', 'you', 'he', 'she', 'it', 'we', 'they', 'them', 'their', 'there',
    'what', 'which', 'who', 'whom', 'whose', 'when', 'where', 'why', 'how',
    'all', 'some', 'any', 'each', 'every', 'both', 'few', 'many', 'most',
    'other', 'another', 'more', 'less', 'much', 'very', 'too', 'also',

    # Spanish
    'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas',
    'de', 'del', 'al', 'en', 'con', 'por', 'para', 'sin', 'sobre',
    'y', 'o', 'pero', 'que', 'como', 'si', 'no',

    # French
    'le', 'la', 'les', 'un', 'une', 'des',
    'de', 'du', 'au', 'aux', 'en', 'dans', 'sur', 'avec', 'sans', 'pour', 'par',
    'et', 'ou', 'mais', 'que', 'qui', 'dont', 'où',

    # Italian
    'il', 'lo', 'la', 'i', 'gli', 'le', 'un', 'uno', 'una',
    'di', 'da', 'in', 'con', 'su', 'per', 'tra', 'fra',
    'e', 'o', 'ma', 'che', 'chi', 'cui',

    # Portuguese
    'o', 'a', 'os', 'as', 'um', 'uma', 'uns', 'umas',
    'de', 'da', 'do', 'em', 'no', 'na', 'com', 'por', 'para', 'sem',
    'e', 'ou', 'mas', 'que', 'se',

    # German
    'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'einem', 'einen',
    'und', 'oder', 'aber', 'von', 'mit', 'zu', 'in', 'an', 'auf', 'für',

    # File extensions and common file-related terms
    'pdf', 'epub', 'cbz', 'cbr', 'mobi', 'azw', 'azw3',
    'vol', 'volume', 'issue', 'no', 'v', 'ed', 'edition', 'ebook', 'book',

    # Common number words
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
    'first', 'second', 'third', 'fourth', 'fifth',
}

# Patterns that look like dates or numbers (probably not tags)
DATE_PATTERN = re.compile(r'^\d{2,4}[-/.]\d{1,2}([-/.]\d{1,4})?$')
NUMBER_PATTERN = re.compile(r'^\d+$')


def extract_potential_tags(filename: str, caption: str | None = None) -> Set[str]:
    """Extract potential tags from filename and optional caption.

    Returns a set of candidate tags (individual words, not phrases).
    """
    tags = set()

    # Remove file extension from filename
    name = Path(filename).stem

    # Combine filename and caption for processing
    text = name
    if caption:
        text += " " + caption

    # Split on ALL non-alphanumeric characters (including spaces, underscores, dashes, etc.)
    # This gives us individual words only
    parts = SEPARATORS.split(text)

    # Process each part
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Skip if too short
        if len(part) < 3:
            continue

        # Convert to lowercase for comparison
        part_lower = part.lower()

        # Skip if it's a stopword (articles, prepositions, common words)
        if part_lower in STOPWORDS:
            continue

        # Skip if it's only digits (years, issue numbers, etc.)
        if part.isdigit():
            continue

        # Skip if it looks like a date pattern
        if DATE_PATTERN.match(part):
            continue

        # Skip if it contains ANY digits (e.g., "5th", "2nd", "v2", "1st")
        if any(c.isdigit() for c in part):
            continue

        # Add the lowercase version
        tags.add(part_lower)

    return tags


def _clean_tag(text: str) -> str:
    """Clean and normalize a tag by removing all whitespace and converting to lowercase."""
    # Remove all whitespace (since we want single-word tags only)
    text = re.sub(r'\s+', '', text)

    # Convert to lowercase for consistency
    text = text.lower()

    return text


def _extract_multi_word_tags(text: str) -> Set[str]:
    """Extract multi-word phrases that appear before common delimiters.

    Examples:
        "The Economist - 2026-01-15" -> "The Economist"
        "Stephen King - The Stand" -> "Stephen King"
        "Magazine Name 2026" -> "Magazine Name"
    """
    tags = set()

    # Pattern: capture everything before " - " (common author/title separator)
    match = re.match(r'^(.+?)\s*-\s*', text)
    if match:
        candidate = match.group(1).strip()
        if _is_valid_multi_word_tag(candidate):
            cleaned = _clean_tag(candidate)
            if cleaned:
                tags.add(cleaned)

    # Pattern: capture everything before year (magazine pattern)
    match = re.match(r'^(.+?)\s+(19|20)\d{2}', text)
    if match:
        candidate = match.group(1).strip()
        if _is_valid_multi_word_tag(candidate):
            cleaned = _clean_tag(candidate)
            if cleaned:
                tags.add(cleaned)

    # Pattern: capture everything before date pattern YYYY-MM
    match = re.match(r'^(.+?)\s+(19|20)\d{2}[-_]\d{2}', text)
    if match:
        candidate = match.group(1).strip()
        if _is_valid_multi_word_tag(candidate):
            cleaned = _clean_tag(candidate)
            if cleaned:
                tags.add(cleaned)

    return tags


def _is_valid_multi_word_tag(text: str) -> bool:
    """Check if a multi-word phrase is a valid tag candidate."""
    # Must have at least 2 characters
    if len(text) < 2:
        return False

    # Must contain at least one letter
    if not re.search(r'[a-zA-Z]', text):
        return False

    # Must not be all numbers
    if text.replace(' ', '').replace('.', '').isdigit():
        return False

    return True


def normalize_tag(tag: str) -> str:
    """Normalize a tag for comparison (lowercase, strip whitespace)."""
    return tag.strip().lower()
