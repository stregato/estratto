"""Kavita API integration: JWT auth, library scan triggers with retry/backoff."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("estratto.kavita")


class KavitaError(Exception):
    pass


@dataclass
class KavitaClient:
    base_url: str
    api_key: str
    retry_attempts: int = 5
    retry_backoff_seconds: int = 5

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._jwt: Optional[str] = None

    # Auth --------------------------------------------------------------

    def authenticate(self) -> str:
        """Authenticate with the API key against /api/Plugin/authenticate to get a JWT."""
        url = f"{self.base_url}/api/Plugin/authenticate"
        params = {"apiKey": self.api_key, "pluginName": "Estratto"}

        def _do():
            resp = requests.post(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token") or data.get("Token")
            if not token:
                raise KavitaError(f"No token in authenticate response: {data}")
            return token

        self._jwt = self._with_retry(_do, "authenticate")
        logger.info("Authenticated with Kavita")
        return self._jwt

    def _headers(self) -> dict:
        if not self._jwt:
            self.authenticate()
        return {"Authorization": f"Bearer {self._jwt}", "Content-Type": "application/json"}

    # Scans ---------------------------------------------------------------

    def scan_library(self, library_id: int) -> bool:
        """Trigger a scan for a specific library. Returns True on success."""
        url = f"{self.base_url}/api/Library/scan"

        def _do():
            resp = requests.post(url, params={"libraryId": library_id}, headers=self._headers(), timeout=30)
            if resp.status_code == 401:
                # Token likely expired; refresh once and retry within this attempt.
                self.authenticate()
                resp = requests.post(url, params={"libraryId": library_id}, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            return True

        try:
            self._with_retry(_do, f"scan_library({library_id})")
            logger.info("Triggered scan for library %s", library_id)
            return True
        except KavitaError as exc:
            logger.error("scan_library(%s) failed after retries: %s", library_id, exc)
            return False

    def scan_all(self) -> bool:
        """Fallback: trigger a scan of all libraries."""
        url = f"{self.base_url}/api/Library/scan-all"

        def _do():
            resp = requests.post(url, headers=self._headers(), timeout=30)
            if resp.status_code == 401:
                self.authenticate()
                resp = requests.post(url, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            return True

        try:
            self._with_retry(_do, "scan_all")
            logger.info("Triggered scan-all")
            return True
        except KavitaError as exc:
            logger.error("scan_all failed after retries: %s", exc)
            return False

    def trigger_scan(self, library_id: Optional[int]) -> bool:
        """Try a per-library scan; fall back to scan-all if that fails or no ID given."""
        if library_id is not None:
            if self.scan_library(library_id):
                return True
            logger.warning("Falling back to scan-all after per-library scan failure")
        return self.scan_all()

    # Retry helper ----------------------------------------------------------

    def _with_retry(self, fn, description: str):
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if attempt < self.retry_attempts:
                    wait = self.retry_backoff_seconds * attempt
                    logger.warning(
                        "%s failed (attempt %d/%d): %s; retrying in %ds",
                        description, attempt, self.retry_attempts, exc, wait,
                    )
                    time.sleep(wait)
        raise KavitaError(f"{description} failed after {self.retry_attempts} attempts: {last_exc}")
