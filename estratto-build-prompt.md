# Estratto — Build Prompt

Build a Python service called **Estratto** that watches a Telegram channel for book/article/magazine files, sorts them into the correct folder structure, and triggers a Kavita library scan so they show up automatically.

## Core requirements

**1. Telegram ingestion**
- Use Telethon (MTProto client, not Bot API) to connect with a user session, since bot accounts can't reliably read full channel history or large files.
- Support two modes: (a) a one-time backfill that iterates a channel's full message history, and (b) a long-running listener that reacts to new messages as they arrive.
- Filter incoming messages for documents matching these extensions: `.epub`, `.pdf`, `.cbz`, `.cbr`, `.mobi`.
- Download matched files into a local staging directory, preserving the original filename plus the Telegram message ID (to avoid collisions and allow idempotent re-runs).
- Keep a small local SQLite table of `message_id -> status` (downloaded / sorted / scanned / failed) so restarts don't re-process the same files.

**2. Metadata extraction**
- For EPUB files: use `ebooklib` to pull title, author, and series (if present in OPF metadata).
- For PDF files: try `pypdf` or `PyMuPDF` for embedded title/author metadata first; if empty or unreliable, fall back to parsing the Telegram caption or filename with a configurable regex (e.g. `Author - Title.pdf` or `MagazineName - YYYY-MM.pdf`).
- For CBZ/CBR (comics/manga): fall back to filename parsing only.
- Normalize extracted titles/authors (strip illegal filesystem characters, trim whitespace, title-case where appropriate).

**3. Content classification via LLM (OpenAI)**
- After basic metadata extraction (title/author/etc.), send a lightweight classification request to the OpenAI API to determine:
  - **Content type**: book / magazine / comic-or-manga / academic-article — used to pick which top-level library it belongs to.
  - **Category/genre**: e.g. Fiction, Non-Fiction, Cooking, Photography, Science, History, Technology — used as a subfolder under the author or series.
- Build the classification prompt from whatever signal is available: filename, extracted title/author, first page or two of extracted text (for PDFs/EPUBs use `pypdf`/`ebooklib` to pull a short text excerpt), and any Telegram caption. Keep the excerpt short (a few hundred words at most) — this is just a classification signal, not something to reproduce.
- Request a strict structured response (JSON mode or function-calling) with fields like `{"content_type": ..., "category": ..., "confidence": ...}` so it's directly usable in code without free-text parsing.
- If confidence is below a configurable threshold, route the file to `needs-review/` instead of guessing.
- Make the OpenAI model and API key configurable; keep this step optional (a config flag) so the tool still works with just filename/metadata-based sorting if no API key is set.

**4. Sorting into Kavita's expected layout**
- Config-driven mapping of content type -> library root path, now incorporating the LLM-derived category, e.g.:
  - Books -> `/kavita-library/Books/{Category}/{Author}/{Title}/{Title}.{ext}`
  - Magazines -> `/kavita-library/Magazines/{Category}/{MagazineName}/{Year}/{MagazineName} {Issue}.{ext}`
  - Comics -> `/kavita-library/Comics/{Category}/{Series}/{Series} {Volume}.{ext}`
- Respect Kavita's rule that each series/title must be fully self-contained in its own folder — never leave loose files at a library root.
- Move (not copy) files from staging into the final path once metadata is resolved; log every move, including which fields came from filename/metadata parsing vs. LLM classification.

**5. Kavita API integration**
- Config values: Kavita base URL, API key, and a mapping of content-type -> Kavita library ID.
- On startup, authenticate against `/api/Plugin/authenticate` with the API key to get a JWT, and refresh it as needed.
- After moving a batch of files, call the library scan endpoint for the relevant library ID; fall back to `scan-all` if a per-library scan call fails.
- Debounce scan calls — batch files that arrive within a short window (e.g. 60s) into a single scan trigger rather than one scan per file.

**6. Config & structure**
- Single `config.yaml` (or `.env`) covering: Telegram API ID/hash, session name, channel identifier, staging dir, Kavita base URL + API key, library ID mappings, filename-parsing regex patterns.
- Project layout:
  ```
  estratto/
    config.yaml
    estratto/
      __init__.py
      telegram_client.py   # Telethon connection + download logic
      metadata.py          # EPUB/PDF/filename parsing
      sorter.py            # path resolution + file moves
      kavita_client.py     # auth + scan trigger
      db.py                # SQLite state tracking
      main.py              # CLI entrypoint, wires it all together
    requirements.txt
    README.md
  ```
- CLI entrypoints: `estratto backfill` (process channel history once) and `estratto listen` (run continuously).
- Structured logging (not just print) so it's usable as a systemd service or Docker container.

**7. Error handling**
- Any file that fails metadata parsing goes to a `needs-review/` folder instead of being guessed into a wrong path, with a log entry explaining why.
- Retry Kavita API calls with backoff; if Kavita is unreachable, keep files safely sorted on disk and retry the scan trigger later rather than losing track of them.

## Deliverable
Working code for all files above, a requirements.txt, and a README with setup steps (getting a Telegram API ID/hash, first-run login flow for Telethon, and how to get a Kavita API key).

Ask me before making assumptions about my specific folder layout or library IDs — use placeholders and note where I need to fill in real values.
