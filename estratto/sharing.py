"""Deliver an independent, recipient-encrypted copy of a stored document."""
import secrets
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import auth
from .db import Database
from .profiles import ProfileStore


def recipients(auth_path, sender):
    with auth.connect(auth_path) as db:
        rows = db.execute('''SELECT recipient FROM file_shares WHERE sender = ?
            GROUP BY recipient ORDER BY MAX(shared_at) DESC, recipient''', (sender,)).fetchall()
    return [row['recipient'] for row in rows]


def share_file(auth_path, base_dir, sender, message_id, email):
    email = auth.normalize_email(email)
    if email == sender['email']:
        raise HTTPException(400, "Choose another user's email")
    source_store = ProfileStore.from_profile(base_dir, sender['profile_secret'])
    with Database(source_store.db_path) as source_db:
        entry = source_db.get_catalog_entry(message_id)
        record = source_db.get_record(message_id)
    if not entry or not record:
        raise HTTPException(404, "Download the file before sharing it")
    source_path = Path(record.final_path or record.staging_path or '')
    if not source_path.is_dir() or source_path.resolve().parent != source_store.files_dir:
        raise HTTPException(404, "File is no longer available. Download it again before sharing.")

    destination = None
    try:
        with auth.connect(auth_path) as db:
            # Serialize duplicate shares and inactivity cleanup. Both account and
            # recipient catalog writes commit in the same attached-DB transaction.
            db.execute('BEGIN IMMEDIATE')
            recipient = db.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
            if not recipient:
                raise HTTPException(404, "No account found for this email. Ask the recipient to register first.")
            store = ProfileStore.from_profile(base_dir, recipient['profile_secret'])
            with Database(store.db_path):
                pass
            db.execute('ATTACH DATABASE ? AS recipient_db', (str(store.db_path),))
            previous = db.execute('''SELECT recipient_message_id FROM file_shares
                WHERE sender = ? AND recipient = ? AND message_id = ?''',
                (sender['email'], email, message_id)).fetchone()
            if previous:
                existing = db.execute('SELECT staging_path FROM recipient_db.files WHERE message_id = ?',
                                      (previous['recipient_message_id'],)).fetchone()
                if existing and Path(existing['staging_path']).is_dir():
                    return {'status': 'already_shared', 'email': email}
            copied_id = previous['recipient_message_id'] if previous else -secrets.randbits(62) - 1
            while not previous and db.execute('SELECT 1 FROM recipient_db.catalog WHERE message_id = ?', (copied_id,)).fetchone():
                copied_id = -secrets.randbits(62) - 1
            manifest = source_store.load_file_manifest(source_path)
            # A private temporary file keeps large documents out of memory and is
            # removed on success or failure; only the encrypted copy persists.
            with tempfile.NamedTemporaryFile(dir=store.temp_dir) as plain:
                for index in range(int(manifest['chunk_count'])):
                    plain.write(source_store.decrypt_file_chunk(sender['profile_secret'], source_path, index, manifest=manifest))
                plain.flush()
                destination = store.files_dir / f'shared-{abs(copied_id)}.estratto'
                store.encrypt_file(recipient['profile_secret'], Path(plain.name), f'shared-{abs(copied_id)}')
            db.execute('''INSERT INTO recipient_db.catalog
                (message_id, filename, caption, size, message_date, ext, source)
                VALUES (?, ?, ?, ?, ?, ?, 'shared')
                ON CONFLICT(message_id) DO UPDATE SET filename=excluded.filename,
                size=excluded.size, message_date=excluded.message_date''',
                (copied_id, entry['filename'], f"Shared by {sender['email']}",
                 manifest['plaintext_size'], datetime.now(timezone.utc).isoformat(), entry['ext']))
            db.execute('''INSERT INTO recipient_db.files
                (message_id, channel, original_filename, staging_path, status)
                VALUES (?, 'shared', ?, ?, 'downloaded')
                ON CONFLICT(message_id) DO UPDATE SET staging_path=excluded.staging_path,
                final_path=NULL, status='downloaded', error=NULL''',
                (copied_id, entry['filename'], str(destination)))
            db.execute('''INSERT INTO file_shares VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sender, recipient, message_id) DO UPDATE SET shared_at=excluded.shared_at''',
                (sender['email'], email, message_id, copied_id, time.time()))
            # Incoming shares do not count as the recipient logging in. Allow the
            # next inactivity sweep to remove new files for an inactive account.
            db.execute('UPDATE accounts SET files_cleaned = 0 WHERE email = ?', (email,))
        return {'status': 'shared', 'email': email}
    except Exception:
        if destination and destination.exists():
            shutil.rmtree(destination)
        raise
