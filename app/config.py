from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(f"TERRAMETRIC_{name}")
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./terrametric.db"
    max_upload_bytes: int = 50 * 1024 * 1024
    max_unzipped_bytes: int = 250 * 1024 * 1024
    max_zip_members: int = 200
    max_features: int = 100_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("TERRAMETRIC_DATABASE_URL", cls.database_url),
            max_upload_bytes=_env_int("MAX_UPLOAD_MB", 50) * 1024 * 1024,
            max_unzipped_bytes=_env_int("MAX_UNZIPPED_MB", 250) * 1024 * 1024,
            max_zip_members=_env_int("MAX_ZIP_MEMBERS", 200),
            max_features=_env_int("MAX_FEATURES", 100_000),
        )
