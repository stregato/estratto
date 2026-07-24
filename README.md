# Estratto

Downloads and organizes documents shared through Telegram and arXiv, with a web UI for
indexing sources, selecting what to download, browsing the local catalog, and reading PDFs,
EPUBs, and comics in place. Designed to run well on a Raspberry Pi 4.

## How it works

1. **Ingestion** (`estratto/telegram_client.py`) — a Telethon *user* session (not a bot)
   watches a channel: backfill full history, listen for new messages, or index channel
   documents from the web UI without downloading everything up front.
2. **Source search** (`estratto/arxiv_client.py`) — searches arXiv on demand from the web UI,
   then downloads chosen PDFs into local storage.
3. **State** (`estratto/db.py`) — a local SQLite database tracks indexed items, downloads,
   tags, and reading progress so reloads and restarts do not lose context.
4. **Web UI** (`estratto/webapp.py`, `web/static/`) — FastAPI app serving a single-page
   frontend with source screens for Telegram and arXiv, a shared catalog, saved document tabs,
   and an embedded reader with persistent position.

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Telegram API ID/hash — usually nothing to do here

`config.yaml` ships with Telegram Desktop's own publicly-documented API ID/Hash (published
in its open-source repo), so Estratto can talk to Telegram out of the box — you don't need
to sign up for anything to get started, just log in with your phone number (next step).

This is a shared key, though: if you ever hit rate limits, or would rather use a private
one, get a free pair from <https://my.telegram.org> -> **API Development Tools** (log in
with the phone number you'll use with Estratto, create an app, any name/platform is fine)
and put the `api_id`/`api_hash` it gives you into `config.yaml`, the
`ESTRATTO_TELEGRAM_API_ID` / `ESTRATTO_TELEGRAM_API_HASH` env vars, or the web UI's
Configuration tab (Telegram application section) — takes effect immediately, no restart.

### 3. First-run Telegram login

**Via the web UI (recommended):** start `estratto web` (see below), open it in a browser,
go to the **Telegram Login** tab, and enter your phone number, the login code Telegram
sends you, and your 2FA password if you have one enabled.

**Via the CLI:** the first time you run `estratto backfill` or `estratto listen`, Telethon
prompts for the same information directly in the terminal.

Either way this creates a `<session_name>.session` file (name set by `telegram.session_name`
in config) in the working directory — **keep this file secret**, it's equivalent to being
logged into your Telegram account. Subsequent runs reuse it without prompting.

### 4. Fill in the rest of `config.yaml`

**You need to edit these before running — they're placeholders:**
- `telegram.channel` — the channel username, `t.me/...` link, or numeric chat ID to watch
- `libraries.*.root` — actual filesystem paths Kavita is configured to scan
- `kavita.library_ids` — real library IDs from step 4
- `openai.api_key` / `openai.enabled` — only needed if you want LLM classification

The `filename_patterns` and path `template`s are reasonable defaults per the spec, but
adjust them if your filenames or desired folder structure differ. All of this can also be
edited from the web UI's Configuration tab, which saves back to `config.yaml` (preserving
the file's comments). Changes to `telegram.*`, `staging.*`, or `db.path` need a service
restart to take effect; everything else is picked up on the next action.

### 6. Run it

```bash
# Web UI: browse the channel, pick files to download, configure everything, watch status
python -m estratto.main web
# then open http://<host>:8000 (bind address/port set by config.yaml's `web` section)

# CLI, headless, no UI:
python -m estratto.main backfill   # one-time: process the channel's full history
python -m estratto.main listen     # long-running: react to new messages as they arrive
```

All subcommands accept `-c /path/to/config.yaml` if you keep config outside the working
directory. Don't run `estratto listen` and the web UI's "Start live listener" toggle at the
same time against the same session — pick one, since they'd both drive the same Telethon
connection.

## Docker

The repo now includes a container image that is suitable for Raspberry Pi OS 64-bit
(`arm64`) as well as regular x86_64 Linux.

### Quick start with Docker Compose

```bash
git clone <your-fork-or-copy-of-this-repo> ~/estratto
cd ~/estratto
sudo mkdir -p /var/estratto
sudo chown -R $USER:$USER /var/estratto
docker compose up --build -d
```

On first start the container copies the bundled `config.yaml` to
`/var/estratto/config.yaml`. Edit that file with your real values, then restart:

```bash
docker compose restart
```

Open `http://<pi-ip>:8001`, then use the Telegram source screen to log in and index files.

### What persists

Everything important lives under `/data` inside the container, which is mapped to
`/var/estratto` by `docker-compose.yml`:

- `config.yaml`
- `estratto.db`
- `<session_name>.session`
- `staging/`

This matters because Estratto currently resolves relative paths against the process working
directory; the container runs from `/data` so its behavior matches the non-container setup.

### Plain docker run

```bash
docker build -t estratto .
docker run -d \
  --name estratto \
  -p 8001:8001 \
  -v /var/estratto:/data \
  --restart unless-stopped \
  estratto web
```

### Notes for Raspberry Pi

- Use a 64-bit Raspberry Pi OS image. On July 24, 2026, this is still the practical choice
  for `PyMuPDF` wheels on Raspberry Pi.
- If you use host networking or different published ports, keep `web.port` in `config.yaml`
  aligned with what the container actually listens on.

## Running on a Raspberry Pi 4 (systemd)

Tested against Raspberry Pi OS Bookworm (64-bit) with the Python 3.11 it ships. Use the
**64-bit** OS image — `PyMuPDF`'s prebuilt wheels are for `aarch64`; on 32-bit
(`armv7l`) Raspberry Pi OS, pip may need to compile it from source, which is slow and
sometimes fails on a Pi 4's limited RAM.

```bash
git clone <your-fork-or-copy-of-this-repo> ~/estratto
cd ~/estratto
cp config.yaml config.yaml   # edit config.yaml with your values first, or do it via the web UI later
bash deploy/install.sh web   # or: bash deploy/install.sh listen  for the headless-only unit
sudo systemctl start estratto-web.service
sudo journalctl -u estratto-web.service -f
```

`deploy/install.sh`:
- creates a `venv/` in the project directory and installs `requirements.txt` into it
- installs a systemd unit (`deploy/estratto-web.service.template` or
  `estratto-listen.service.template`) pointed at that venv, running as the invoking user
- enables (but does not start) the service, so you can review config first

Open `http://<pi-ip>:8000` from any device on your LAN once the service is running, log
into Telegram from the Login tab, then use the Catalog tab to index the channel and pick
files to download. Set `web.host: "127.0.0.1"` in `config.yaml` instead of `0.0.0.0` if you
only want to reach the UI via an SSH tunnel rather than exposing it on the LAN.

Resource notes for a Pi 4: the systemd units cap memory at 768MB (web) / 512MB (listen) —
raise `MemoryMax` in the unit file if large PDFs get OOM-killed during text-excerpt
extraction. SQLite, Telethon, and the FastAPI server are all lightweight; the main cost is
PyMuPDF/pypdf parsing big PDFs, which is I/O- and single-core-bound on a Pi 4 but works fine.

## Error handling

- Files that fail metadata parsing, LLM classification (low confidence or errors), or
  sorting (missing required fields for the template) are moved to `needs-review/` and
  logged with a reason — never guessed into a wrong path.
- Kavita scan calls retry with backoff; if Kavita is unreachable, sorted files stay safely
  on disk and the scan is retried on the next debounce cycle rather than being dropped.

## Project layout

```
estratto/
  config.yaml
  estratto/
    __init__.py
    config.py            # config.yaml loading, env var overrides, round-trip save for the web UI
    telegram_client.py    # Telethon connection, indexing, download, login-flow logic
    metadata.py           # EPUB/PDF/filename parsing
    classifier.py         # OpenAI content classification
    sorter.py              # path resolution + file moves
    kavita_client.py       # auth + scan trigger
    db.py                  # SQLite state tracking + channel catalog
    webapp.py               # FastAPI backend for the web UI
    main.py                 # CLI entrypoint: backfill / listen / web
  web/
    static/
      index.html            # single-page UI: catalog, status, login, config
      app.js
      style.css
  deploy/
    install.sh                          # systemd install script (Pi 4 / any Debian-based Linux)
    estratto-web.service.template
    estratto-listen.service.template
  requirements.txt
  README.md
```
