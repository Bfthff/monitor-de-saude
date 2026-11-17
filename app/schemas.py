"""Schemas Pydantic de validação e serialização para os dados de sono."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.config import settings

VALID_STAGES = {"deep", "light", "rem", "awake"}


def _to_local_naive(dt: datetime | None, tz_name: str) -> datetime | None:
    """Normaliza datetimes com fuso para o fuso configurado sem tzinfo."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    return dt


class SleepStageItem(BaseModel):
    """Segmento individual de estágio do sono (compatível com Health Connect)."""

    stage: Literal["deep", "light", "rem", "awake"]
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_minutes: float | None = Field(default=None, ge=0, le=1440)

    @field_validator("stage", mode="before")
    @classmethod
    def normalize_stage(cls, value: str) -> str:
        """Normaliza nomes de estágios vindos em maiúsculas ou variantes."""
        normalized = str(value).strip().lower()
        aliases = {
            "sleeping_deep": "deep",
            "sleeping_light": "light",
            "sleeping_rem": "rem",
            "awakening": "awake",
            "out_of_bed": "awake",
        }
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def validate_duration(self) -> "SleepStageItem":
        """Calcula a duração do estágio por start_time/end_time quando omitida."""
        tz_name = settings.TIMEZONE
        self.start_time = _to_local_naive(self.start_time, tz_name)
        self.end_time = _to_local_naive(self.end_time, tz_name)
        if self.duration_minutes is None:
            if self.start_time is not None and self.end_time is not None:
                delta = (self.end_time - self.start_time).total_seconds() / 60.0
                if delta < 0:
                    raise ValueError("end_time do estágio deve ser posterior a start_time.")
                self.duration_minutes = round(delta, 2)
            else:
                raise ValueError("Informe duration_minutes ou start_time/end_time para o estágio.")
        return self


class SleepIn(BaseModel):
    """Payload de entrada flexível para registro, importação ou webhook de sono."""

    sleep_date: date | None = None
    bedtime: datetime | None = None
    wake_time: datetime | None = None

    duration_minutes: float | None = Field(default=None, gt=0, le=1440)
    duration_hours: float | None = Field(default=None, gt=0, le=24)
    time_in_bed_minutes: float | None = Field(default=None, gt=0, le=1440)

    deep_sleep_minutes: float | None = Field(default=None, ge=0, le=1440)
    light_sleep_minutes: float | None = Field(default=None, ge=0, le=1440)
    rem_sleep_minutes: float | None = Field(default=None, ge=0, le=1440)
    awake_minutes: float | None = Field(default=None, ge=0, le=1440)

    sleep_efficiency: float | None = Field(default=None, ge=0, le=100)
    sleep_score: float | None = Field(default=None, ge=0, le=100)
    resting_hr: float | None = Field(default=None, ge=25, le=220)
    hrv_rmssd: float | None = Field(default=None, ge=1, le=350)
    spo2_avg: float | None = Field(default=None, ge=50, le=100)
    respiratory_rate: float | None = Field(default=None, ge=4, le=60)
    skin_temp_delta: float | None = Field(default=None, ge=-10, le=10)

    subjective_feeling: int | None = Field(default=None, ge=1, le=5)
    source: str = Field(default="manual", max_length=50)
    notes: str | None = None
    stages: list[SleepStageItem] | None = None

    @model_validator(mode="after")
    def resolve_sleep_fields(self) -> "SleepIn":
        """Infere data, horários e duração de sono a partir dos campos disponíveis."""
        tz_name = settings.TIMEZONE
        self.bedtime = _to_local_naive(self.bedtime, tz_name)
        self.wake_time = _to_local_naive(self.wake_time, tz_name)
        self.source = (self.source or "manual").strip()[:50]

        if self.stages:
            stage_totals = {s: 0.0 for s in VALID_STAGES}
            stage_starts: list[datetime] = []
            stage_ends: list[datetime] = []
            for item in self.stages:
                stage_totals[item.stage] += float(item.duration_minutes or 0.0)
                if item.start_time is not None:
                    stage_starts.append(item.start_time)
                if item.end_time is not None:
                    stage_ends.append(item.end_time)

            if self.deep_sleep_minutes is None and stage_totals["deep"] > 0:
                self.deep_sleep_minutes = round(stage_totals["deep"], 2)
            if self.light_sleep_minutes is None and stage_totals["light"] > 0:
                self.light_sleep_minutes = round(stage_totals["light"], 2)
            if self.rem_sleep_minutes is None and stage_totals["rem"] > 0:
                self.rem_sleep_minutes = round(stage_totals["rem"], 2)
            if self.awake_minutes is None and stage_totals["awake"] > 0:
                self.awake_minutes = round(stage_totals["awake"], 2)

            if self.bedtime is None and stage_starts:
                self.bedtime = min(stage_starts)
            if self.wake_time is None and stage_ends:
                self.wake_time = max(stage_ends)

        if self.sleep_date is None:
            if self.wake_time is not None:
                self.sleep_date = self.wake_time.date()
            else:
                self.sleep_date = datetime.now(ZoneInfo(tz_name)).date()

        stage_sum = sum(
            val
            for val in (self.deep_sleep_minutes, self.light_sleep_minutes, self.rem_sleep_minutes)
            if val is not None
        )
        has_sleep_stages = any(
            val is not None
            for val in (self.deep_sleep_minutes, self.light_sleep_minutes, self.rem_sleep_minutes)
        )

        if self.duration_minutes is None:
            if self.duration_hours is not None:
                self.duration_minutes = round(self.duration_hours * 60.0, 2)
            elif self.bedtime is not None and self.wake_time is not None:
                span_minutes = (self.wake_time - self.bedtime).total_seconds() / 60.0
                if span_minutes <= 0:
                    raise ValueError("wake_time deve ser posterior a bedtime.")
                if self.awake_minutes is not None:
                    self.duration_minutes = round(span_minutes - self.awake_minutes, 2)
                elif has_sleep_stages and 0 < stage_sum <= span_minutes:
                    self.duration_minutes = round(stage_sum, 2)
                else:
                    self.duration_minutes = round(span_minutes, 2)
            elif has_sleep_stages and stage_sum > 0:
                self.duration_minutes = round(stage_sum, 2)

        if self.duration_minutes is None or not (0 < self.duration_minutes <= 1440):
            raise ValueError(
                "Não foi possível determinar duration_minutes válida (> 0 e <= 1440 minutos)."
            )

        self.duration_minutes = round(float(self.duration_minutes), 2)
        return self


class SleepOut(BaseModel):
    """Schema de resposta com o registro de sono persistido."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sleep_date: date
    bedtime: datetime | None = None
    wake_time: datetime | None = None
    duration_minutes: float
    time_in_bed_minutes: float | None = None
    deep_sleep_minutes: float | None = None
    light_sleep_minutes: float | None = None
    rem_sleep_minutes: float | None = None
    awake_minutes: float | None = None
    sleep_efficiency: float | None = None
    sleep_score: float | None = None
    resting_hr: float | None = None
    hrv_rmssd: float | None = None
    spo2_avg: float | None = None
    respiratory_rate: float | None = None
    skin_temp_delta: float | None = None
    subjective_feeling: int | None = None
    source: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def duration_hours(self) -> float:
        """Duração total dormida em horas."""
        return round(self.duration_minutes / 60.0, 2)


class SleepBatchIn(BaseModel):
    """Lote de registros de sono para importação em massa."""

    records: list[SleepIn] = Field(..., min_length=1)


class StepsIn(BaseModel):
    """Payload flexível para entrada diária de passos (Android / Samsung Health / manual)."""

    step_date: date | None = None
    steps_count: int | None = Field(default=None, ge=0, le=250000)
    steps: int | None = Field(default=None, ge=0, le=250000)
    goal_steps: int | None = Field(default=None, ge=100, le=200000)

    distance_km: float | None = Field(default=None, ge=0, le=300)
    distance_meters: float | None = Field(default=None, ge=0, le=300000)
    active_minutes: float | None = Field(default=None, ge=0, le=1440)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    floors_climbed: int | None = Field(default=None, ge=0, le=1000)

    source: str = Field(default="mobile_webhook", max_length=50)
    notes: str | None = None

    @model_validator(mode="after")
    def resolve_steps_fields(self) -> "StepsIn":
        """Resolve aliases do celular, data padrão e estimativas de distância/calorias."""
        if self.steps_count is None and self.steps is not None:
            self.steps_count = self.steps

        if self.steps_count is None or self.steps_count <= 0:
            raise ValueError("Informe steps_count (ou steps) maior que zero.")

        if self.step_date is None:
            self.step_date = datetime.now(ZoneInfo(settings.TIMEZONE)).date()

        if self.goal_steps is None:
            self.goal_steps = settings.STEPS_TARGET

        if self.distance_km is None:
            if self.distance_meters is not None:
                self.distance_km = round(self.distance_meters / 1000.0, 2)
            else:
                self.distance_km = round(self.steps_count * 0.00076, 2)

        if self.calories_kcal is None:
            self.calories_kcal = round(self.steps_count * 0.04, 1)

        self.source = (self.source or "mobile_webhook").strip()[:50]
        return self


class StepsOut(BaseModel):
    """Schema de resposta com o registro diário de passos."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    step_date: date
    steps_count: int
    goal_steps: int | None = None
    distance_km: float | None = None
    active_minutes: float | None = None
    calories_kcal: float | None = None
    floors_climbed: int | None = None
    source: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def goal_pct(self) -> float:
        """Percentual atingido da meta diária de passos."""
        target = max(self.goal_steps or 10000, 1)
        return round((self.steps_count / target) * 100.0, 1)


class StepsBatchIn(BaseModel):
    """Lote de registros de passos para importação em massa."""

    records: list[StepsIn] = Field(..., min_length=1)
