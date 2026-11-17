"""Regras de negócio e persistência para registros de sono."""

from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SleepLog
from app.schemas import SleepIn


def compute_derived_metrics(data: dict, target_hours: float = 8.0) -> dict:
    """Calcula tempo na cama, eficiência do sono e sleep_score quando ausentes."""
    result = dict(data)
    duration_minutes = float(result["duration_minutes"])
    bedtime: datetime | None = result.get("bedtime")
    wake_time: datetime | None = result.get("wake_time")
    awake_minutes: float | None = result.get("awake_minutes")

    if result.get("time_in_bed_minutes") is None:
        if bedtime is not None and wake_time is not None:
            span = (wake_time - bedtime).total_seconds() / 60.0
            result["time_in_bed_minutes"] = round(max(duration_minutes, span), 2)
        elif awake_minutes is not None:
            result["time_in_bed_minutes"] = round(duration_minutes + awake_minutes, 2)
        else:
            result["time_in_bed_minutes"] = round(duration_minutes, 2)

    time_in_bed = float(result["time_in_bed_minutes"])
    if result.get("sleep_efficiency") is None and time_in_bed > 0:
        efficiency = min(100.0, (duration_minutes / time_in_bed) * 100.0)
        result["sleep_efficiency"] = round(efficiency, 1)

    if result.get("sleep_score") is None:
        duration_hours = duration_minutes / 60.0
        duration_component = min(1.0, duration_hours / max(target_hours, 1.0)) * 50.0

        deep = result.get("deep_sleep_minutes")
        rem = result.get("rem_sleep_minutes")
        if deep is not None or rem is not None:
            restorative = float(deep or 0.0) + float(rem or 0.0)
            restorative_ratio = restorative / duration_minutes if duration_minutes > 0 else 0.0
            architecture_component = min(1.0, restorative_ratio / 0.40) * 30.0
        else:
            architecture_component = 22.0

        eff = float(result.get("sleep_efficiency") or 90.0)
        efficiency_component = 20.0 if eff >= 85.0 else max(0.0, (eff / 85.0) * 20.0)
        if awake_minutes is not None and awake_minutes > 20.0:
            penalty = min(8.0, (awake_minutes - 20.0) * 0.15)
            efficiency_component = max(0.0, efficiency_component - penalty)

        score = duration_component + architecture_component + efficiency_component
        result["sleep_score"] = round(min(100.0, max(0.0, score)), 1)

    return result


def _payload_to_model_dict(payload: SleepIn, target_hours: float) -> dict:
    """Extrai campos persistíveis do schema SleepIn e aplica métricas derivadas."""
    raw_fields = payload.model_dump(exclude={"duration_hours", "stages"})
    return compute_derived_metrics(raw_fields, target_hours=target_hours)


def upsert_sleep_log(
    db: Session,
    payload: SleepIn,
    raw_json: str | None = None,
    commit: bool = True,
) -> SleepLog:
    """Insere ou atualiza o registro de sono identificado por sleep_date."""
    values = _payload_to_model_dict(payload, target_hours=settings.SLEEP_TARGET_HOURS)

    existing = db.scalar(select(SleepLog).where(SleepLog.sleep_date == payload.sleep_date))
    if existing is not None:
        for field, value in values.items():
            setattr(existing, field, value)
        if raw_json is not None:
            existing.raw_payload = raw_json
        record = existing
    else:
        if raw_json is not None:
            values["raw_payload"] = raw_json
        record = SleepLog(**values)
        db.add(record)

    if commit:
        db.commit()
        db.refresh(record)
    else:
        db.flush()

    return record


def upsert_sleep_batch(db: Session, records: list[SleepIn]) -> list[SleepLog]:
    """Processa um lote de registros de sono em uma única transação."""
    saved: list[SleepLog] = []
    for item in records:
        record = upsert_sleep_log(
            db,
            item,
            raw_json=item.model_dump_json(),
            commit=False,
        )
        saved.append(record)
    db.commit()
    for record in saved:
        db.refresh(record)
    return saved


def list_sleep_logs(db: Session, limit: int = 90) -> list[SleepLog]:
    """Lista os registros de sono ordenados da data mais recente para a mais antiga."""
    stmt = select(SleepLog).order_by(SleepLog.sleep_date.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_sleep_log_by_date(db: Session, sleep_date: date) -> SleepLog | None:
    """Busca o registro de sono de uma data específica."""
    return db.scalar(select(SleepLog).where(SleepLog.sleep_date == sleep_date))


def delete_sleep_log_by_date(db: Session, sleep_date: date) -> bool:
    """Remove o registro da data especificada, retornando True se existia."""
    existing = get_sleep_log_by_date(db, sleep_date)
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True
