from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_LLM_SETTINGS = {
    "provider": "kimi",
    "display_name": "Kimi K3",
    "model": "kimi-k3",
    "base_url": "https://api.moonshot.cn/v1",
}


class LLMSettingsStore:
    """Persist the active LLM configuration without exposing its API key."""

    def __init__(self, database_path: Path, key_path: Path | None = None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path = Path(key_path or self.database_path.with_name(".settings.key"))
        self._cipher = Fernet(self._load_or_create_master_key())
        self._initialize()

    def _load_or_create_master_key(self) -> bytes:
        configured = os.getenv("DOCUMENT_EXTRACT_MASTER_KEY", "").strip()
        if configured:
            key = configured.encode("utf-8")
            # Constructing a Fernet instance validates the key shape.
            Fernet(key)
            return key

        if self.key_path.exists():
            return self.key_path.read_bytes().strip()

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    provider TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    encrypted_api_key BLOB,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO llm_settings
                    (id, provider, display_name, model, base_url, encrypted_api_key)
                VALUES (1, ?, ?, ?, ?, NULL)
                """,
                (
                    DEFAULT_LLM_SETTINGS["provider"],
                    DEFAULT_LLM_SETTINGS["display_name"],
                    DEFAULT_LLM_SETTINGS["model"],
                    DEFAULT_LLM_SETTINGS["base_url"],
                ),
            )

    def public_settings(self) -> dict[str, Any]:
        row = self._row()
        return {
            "provider": row["provider"],
            "display_name": row["display_name"],
            "model": row["model"],
            "base_url": row["base_url"],
            "has_api_key": row["encrypted_api_key"] is not None,
            "updated_at": row["updated_at"],
        }

    def runtime_settings(self) -> dict[str, Any]:
        row = self._row()
        encrypted = row["encrypted_api_key"]
        api_key = None
        if encrypted is not None:
            try:
                api_key = self._cipher.decrypt(bytes(encrypted)).decode("utf-8")
            except InvalidToken as exc:
                raise RuntimeError("模型配置中的 API Key 无法解密，请在配置页面重新保存") from exc
        return {
            # Kimi exposes an OpenAI-compatible API and Semantica registers that
            # protocol under the openai provider name.
            "provider": "openai" if row["provider"] == "kimi" else row["provider"],
            "model": row["model"],
            "base_url": row["base_url"],
            "api_key": api_key,
        }

    def update(
        self,
        *,
        provider: str,
        display_name: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> dict[str, Any]:
        current = self._row()
        encrypted = current["encrypted_api_key"]
        if clear_api_key:
            encrypted = None
        elif api_key:
            encrypted = self._cipher.encrypt(api_key.encode("utf-8"))

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE llm_settings
                SET provider = ?, display_name = ?, model = ?, base_url = ?,
                    encrypted_api_key = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (provider, display_name, model, base_url, encrypted),
            )
        return self.public_settings()

    def _row(self) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM llm_settings WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("模型配置记录不存在")
        return row
