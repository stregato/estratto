"""Email/PIN accounts and revocable 90-day sessions; no email delivery."""
import hashlib
import hmac
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

SESSION_SECONDS = 90 * 24 * 60 * 60


def normalize_email(value):
    if not isinstance(value, str):
        raise HTTPException(400, "Enter a valid email address")
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(
        r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value
    ) or len(value.split('@')[0]) > 64:
        raise HTTPException(400, "Enter a valid email address")
    return value


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts (
                email TEXT PRIMARY KEY, salt TEXT NOT NULL, pin_hash TEXT NOT NULL,
                profile_secret TEXT NOT NULL UNIQUE, last_seen REAL NOT NULL, files_cleaned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at REAL NOT NULL
            );
        ''')
    path.chmod(0o600)


def authenticate(path, email, pin, registering, client):
    email = normalize_email(email)
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6}", pin):
        raise HTTPException(400, "PIN must contain exactly 6 digits")
    now = time.time()
    # Persist limits across restarts, serializing attempts across workers.
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM attempts WHERE reset_at <= ?', (now,))
        for key, limit in ((f'email:{email}', 5), (f'client:{client}', 30)):
            row = db.execute('SELECT count FROM attempts WHERE key = ?', (key,)).fetchone()
            if row and row['count'] >= limit:
                raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        for key in (f'email:{email}', f'client:{client}'):
            db.execute('INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET count = count + 1', (key, now + 900))
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
        if registering and row:
            raise HTTPException(409, "This email is already registered. Sign in with its PIN.")
        salt = row['salt'] if row else secrets.token_hex(16)
        digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        if registering:
            db.execute('INSERT INTO accounts (email, salt, pin_hash, profile_secret, last_seen) VALUES (?, ?, ?, ?, ?)', (email, salt, digest, secrets.token_urlsafe(48), now))
        elif not row or not hmac.compare_digest(digest, row['pin_hash']):
            raise HTTPException(401, "Email or PIN is incorrect")
        db.execute("UPDATE accounts SET last_seen = ?, files_cleaned = 0 WHERE email = ?", (now, email))
        token = secrets.token_urlsafe(32)
        expires = now + SESSION_SECONDS
        db.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
        db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (token_hash(token), email, expires))
        db.execute('DELETE FROM attempts WHERE key = ?', (f'email:{email}',))
    return {'token': token, 'email': email, 'expires_at': expires}


def session(path, token):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute('''SELECT a.*, s.expires_at FROM sessions s
            JOIN accounts a ON a.email = s.email
            WHERE s.token_hash = ? AND s.expires_at > ?''', (token_hash(token), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "Please sign in")
        db.execute("UPDATE accounts SET last_seen = ?, files_cleaned = 0 WHERE email = ?", (time.time(), row["email"]))
    return dict(row)


def logout(path, token):
    with connect(path) as db:
        db.execute('DELETE FROM sessions WHERE token_hash = ?', (token_hash(token),))


def cleanup_inactive_files(path, base_dir, now=None):
    """Remove file contents after 30 inactive days, retaining accounts and metadata.

    The account transaction prevents sign-in/activity racing the deletion decision.
    Only dedicated file and temp directories are removed, never paths from metadata.
    """
    from .db import Database
    from .profiles import profile_hash

    cutoff = (time.time() if now is None else now) - 30 * 24 * 60 * 60
    removed = 0
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT * FROM accounts WHERE last_seen <= ? AND files_cleaned = 0", (cutoff,)).fetchall()
        for row in rows:
            root = Path(base_dir) / "profiles" / profile_hash(row["profile_secret"])
            if root.is_symlink():
                continue
            for name in ("files", "tmp"):
                folder = root / name
                if folder.is_symlink():
                    folder.unlink()
                elif folder.exists():
                    shutil.rmtree(folder)
            database_path = root / "estratto.db"
            if database_path.exists():
                with Database(database_path) as profile_db:
                    profile_db.clear_file_storage_records()
            db.execute("UPDATE accounts SET files_cleaned = 1 WHERE email = ?", (row["email"],))
            removed += 1
    return removed
