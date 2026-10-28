import os
import shutil
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

REQUIRED = ("DISCORD_TOKEN", "OWNER_ID", "API_CLIENT_ID_OSU", "API_CLIENT_SECRET_OSU")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class R2Settings:
    endpoint: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base_url: str


R2_ENV = {
    "endpoint": "R2_ENDPOINT",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
    "bucket": "R2_BUCKET",
    "public_base_url": "R2_PUBLIC_BASE_URL",
}


@dataclass(frozen=True)
class Settings:
    discord_token: str
    owner_id: int
    osu_client_id: str
    osu_client_secret: str
    ffmpeg_path: str
    ffprobe_path: str
    r2: R2Settings | None


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _load_r2() -> R2Settings | None:
    values = {field.name: _env(R2_ENV[field.name]) for field in fields(R2Settings)}
    if not any(values.values()):
        return None
    missing = [R2_ENV[key] for key, value in values.items() if not value]
    if missing:
        raise ConfigError(f"R2 is partially configured. Missing: {', '.join(missing)}")
    return R2Settings(**values)


@cache
def get_settings() -> Settings:
    """Load and validate settings from the environment and `.env` (once)."""
    load_dotenv(ROOT / ".env")

    missing = [name for name in REQUIRED if not _env(name)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
    if not _env("OWNER_ID").isdigit():
        raise ConfigError("OWNER_ID must be a numeric Discord user ID.")

    return Settings(
        discord_token=_env("DISCORD_TOKEN"),
        owner_id=int(_env("OWNER_ID")),
        osu_client_id=_env("API_CLIENT_ID_OSU"),
        osu_client_secret=_env("API_CLIENT_SECRET_OSU"),
        ffmpeg_path=_env("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=_env("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe",
        r2=_load_r2(),
    )
