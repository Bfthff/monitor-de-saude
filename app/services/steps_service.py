"""Regras de negócio e persistência para registros diários de passos."""

from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyStepsLog
from app.schemas import StepsIn


def upsert_steps_log(
    db: Session,
    payload: StepsIn,
    raw_json: str | None = None,
    commit: bool = True,
) -> DailyStepsLog:
    """Insere ou atualiza o registro diário de passos identificado por step_date."""
    values = payload.model_dump(exclude={"steps", "distance_meters"})

    existing = db.scalar(select(DailyStepsLog).where(DailyStepsLog.step_date == payload.step_date))
    if existing is not None:
        for field, value in values.items():
            setattr(existing, field, value)
        if raw_json is not None:
            existing.raw_payload = raw_json
        record = existing
    else:
        if raw_json is not None:
            values["raw_payload"] = raw_json
        record = DailyStepsLog(**values)
        db.add(record)

    if commit:
        db.commit()
        db.refresh(record)
    else:
        db.flush()

    return record


def upsert_steps_batch(db: Session, records: list[StepsIn]) -> list[DailyStepsLog]:
    """Processa um lote de registros de passos em uma única transação."""
    saved: list[DailyStepsLog] = []
    for item in records:
        record = upsert_steps_log(
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


def list_steps_logs(db: Session, limit: int = 90) -> list[DailyStepsLog]:
    """Lista os registros de passos ordenados da data mais recente para a mais antiga."""
    stmt = select(DailyStepsLog).order_by(DailyStepsLog.step_date.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_steps_log_by_date(db: Session, step_date: date) -> DailyStepsLog | None:
    """Busca o registro de passos de uma data específica."""
    return db.scalar(select(DailyStepsLog).where(DailyStepsLog.step_date == step_date))


def delete_steps_log_by_date(db: Session, step_date: date) -> bool:
    """Remove o registro de passos da data especificada."""
    existing = get_steps_log_by_date(db, step_date)
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True
