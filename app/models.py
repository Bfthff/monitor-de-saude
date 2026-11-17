"""Modelos ORM do banco de dados."""

from datetime import date, datetime, timezone
from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now_naive() -> datetime:
    """Retorna timestamp UTC sem tzinfo para armazenamento consistente em SQLite/Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SleepLog(Base):
    """Registro diário de sono e telemetria fisiológica noturna."""

    __tablename__ = "sleep_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sleep_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    bedtime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wake_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    time_in_bed_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    light_sleep_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_sleep_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    awake_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)

    sleep_efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_rmssd: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiratory_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temp_delta: Mapped[float | None] = mapped_column(Float, nullable=True)

    subjective_feeling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )


class DailyStepsLog(Base):
    """Registro diário de passos e volume ambulatorial."""

    __tablename__ = "daily_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    step_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    steps_count: Mapped[int] = mapped_column(Integer, nullable=False)
    goal_steps: Mapped[int | None] = mapped_column(Integer, nullable=True, default=10000)

    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    floors_climbed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
