from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


PROFILE_HEADER = "X-Estratto-Profile"
PROFILE_HASH_HEADER = "X-Estratto-Profile-Hash"
PROFILE_MIN_LENGTH = 17
SETTINGS_FILENAME = "settings.json.enc"
FILE_CHUNK_SIZE = 1024 * 1024
FILE_MANIFEST_NAME = "manifest.json"


class ProfileError(ValueError):
    pass


def normalize_profile_name(raw: str) -> str:
    value = str(raw or "").strip()
    if len(value) < PROFILE_MIN_LENGTH:
        raise ProfileError(f"Profile name must be at least {PROFILE_MIN_LENGTH} characters long")
    return value


def profile_hash(profile_name: str) -> str:
    normalized = normalize_profile_name(profile_name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _derive_key(profile_name: str) -> bytes:
    normalized = normalize_profile_name(profile_name)
    digest = profile_hash(normalized)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes.fromhex(digest),
        iterations=390000,
    )
    key = kdf.derive(normalized.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


def _derive_file_key(profile_name: str, salt: bytes) -> bytes:
    normalized = normalize_profile_name(profile_name)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return kdf.derive(normalized.encode("utf-8"))


@dataclass
class ProfileStore:
    base_dir: Path
    hash: str

    def __post_init__(self) -> None:
        self.root_dir = (self.base_dir / "profiles" / self.hash).resolve()
        self.temp_dir = self.root_dir / "tmp"
        self.files_dir = self.root_dir / "files"
        self.session_dir = self.root_dir / "telegram-session"
        self.db_path = self.root_dir / "estratto.db"
        self.settings_path = self.root_dir / SETTINGS_FILENAME
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_profile(cls, base_dir: Path, profile_name: str) -> "ProfileStore":
        normalized = normalize_profile_name(profile_name)
        return cls(base_dir=base_dir, hash=profile_hash(normalized))

    def _fernet(self, profile_name: str) -> Fernet:
        return Fernet(_derive_key(profile_name))

    def _encrypt_bytes(self, profile_name: str, data: bytes) -> bytes:
        return self._fernet(profile_name).encrypt(data)

    def _decrypt_bytes(self, profile_name: str, payload: bytes) -> bytes:
        try:
            return self._fernet(profile_name).decrypt(payload)
        except InvalidToken as exc:
            raise ProfileError("Stored profile data could not be decrypted with this profile") from exc

    def telegram_session_name(self) -> str:
        return str(self.session_dir / "session")

    def load_settings(self, profile_name: str) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        payload = self._decrypt_bytes(profile_name, self.settings_path.read_bytes())
        data = json.loads(payload.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def save_settings(self, profile_name: str, data: dict[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=True, sort_keys=True).encode("utf-8")
        self.settings_path.write_bytes(self._encrypt_bytes(profile_name, payload))

    def _file_container_path(self, stored_name: str) -> Path:
        return self.files_dir / f"{stored_name}.estratto"

    def encrypt_file(
        self,
        profile_name: str,
        source_path: Path,
        stored_name: str,
        *,
        chunk_size: int = FILE_CHUNK_SIZE,
    ) -> Path:
        container = self._file_container_path(stored_name)
        if container.exists():
            for child in container.iterdir():
                child.unlink()
        else:
            container.mkdir(parents=True, exist_ok=True)

        salt = os.urandom(16)
        key = _derive_file_key(profile_name, salt)
        cipher = AESGCM(key)
        plaintext_size = source_path.stat().st_size if source_path.exists() else 0
        chunk_count = 0
        effective_chunk_size = max(65536, int(chunk_size))

        with source_path.open("rb") as handle:
            while True:
                chunk = handle.read(effective_chunk_size)
                if not chunk:
                    break
                nonce = os.urandom(12)
                aad = chunk_count.to_bytes(8, "big")
                encrypted = cipher.encrypt(nonce, chunk, aad)
                (container / f"chunk-{chunk_count:06d}.bin").write_bytes(nonce + encrypted)
                chunk_count += 1

        manifest = {
            "version": 1,
            "algorithm": "aes-256-gcm-chunked",
            "chunk_size": effective_chunk_size,
            "chunk_count": chunk_count,
            "plaintext_size": plaintext_size,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "original_name": stored_name,
        }
        (container / FILE_MANIFEST_NAME).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return container

    def decrypt_file_bytes(self, profile_name: str, encrypted_path: Path) -> bytes:
        manifest = self.load_file_manifest(encrypted_path)
        return b"".join(
            self.decrypt_file_chunk(profile_name, encrypted_path, index, manifest=manifest)
            for index in range(int(manifest.get("chunk_count") or 0))
        )

    def load_file_manifest(self, encrypted_path: Path) -> dict[str, Any]:
        manifest_path = encrypted_path / FILE_MANIFEST_NAME
        if not manifest_path.exists():
            raise ProfileError("Encrypted file manifest is missing")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileError("Encrypted file manifest is invalid")
        return data

    def read_encrypted_file_chunk(self, encrypted_path: Path, chunk_index: int) -> bytes:
        chunk_path = encrypted_path / f"chunk-{chunk_index:06d}.bin"
        if not chunk_path.exists():
            raise ProfileError(f"Encrypted chunk {chunk_index} is missing")
        return chunk_path.read_bytes()

    def decrypt_file_chunk(
        self,
        profile_name: str,
        encrypted_path: Path,
        chunk_index: int,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> bytes:
        manifest = manifest or self.load_file_manifest(encrypted_path)
        salt_b64 = manifest.get("salt_b64")
        if not isinstance(salt_b64, str) or not salt_b64:
            raise ProfileError("Encrypted file manifest is invalid")
        payload = self.read_encrypted_file_chunk(encrypted_path, chunk_index)
        if len(payload) < 13:
            raise ProfileError("Encrypted chunk payload is invalid")
        key = _derive_file_key(profile_name, base64.b64decode(salt_b64))
        cipher = AESGCM(key)
        nonce = payload[:12]
        ciphertext = payload[12:]
        aad = chunk_index.to_bytes(8, "big")
        try:
            return cipher.decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise ProfileError("File could not be decrypted with this profile") from exc
