"""Configurações da aplicação carregadas via variáveis de ambiente (.env)."""

from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _ensure_sqlite_dir(database_url: str) -> None:
    """Garante que o diretório pai de bancos SQLite locais exista."""
    if not database_url.startswith("sqlite:///"):
        return
    raw_path = database_url.removeprefix("sqlite:///")
    if raw_path in ("", ":memory:"):
        return
    db_path = Path(raw_path)
    if db_path.parent and str(db_path.parent) != ".":
        db_path.parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    """Configurações centralizadas da aplicação."""

    DATABASE_URL: str
    WEBHOOK_API_KEY: str
    SLEEP_TARGET_HOURS: float
    STEPS_TARGET: int
    TIMEZONE: str
    DEFAULT_LAT: float
    DEFAULT_LON: float


def get_settings() -> Settings:
    """Lê as configurações atuais do ambiente com valores padrão seguros."""
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/fitness_monitor.db").strip()
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    _ensure_sqlite_dir(db_url)

    return Settings(
        DATABASE_URL=db_url,
        WEBHOOK_API_KEY=os.getenv("WEBHOOK_API_KEY", "").strip(),
        SLEEP_TARGET_HOURS=float(os.getenv("SLEEP_TARGET_HOURS", "8.0")),
        STEPS_TARGET=int(os.getenv("STEPS_TARGET", "10000")),
        TIMEZONE=os.getenv("TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
        DEFAULT_LAT=float(os.getenv("DEFAULT_LAT", "-27.6775")),
        DEFAULT_LON=float(os.getenv("DEFAULT_LON", "-48.4828")),
    )


settings = get_settings()
