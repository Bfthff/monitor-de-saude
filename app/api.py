"""API HTTP (Webhook) em FastAPI para coleta e consulta dos dados de sono."""

from contextlib import asynccontextmanager
from datetime import date
import hmac
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, init_db
from app.schemas import SleepBatchIn, SleepIn, SleepOut, StepsBatchIn, StepsIn, StepsOut
from app.services.analytics import (
    build_sleep_analytics_df,
    build_steps_analytics_df,
    build_steps_sleep_relation,
    compute_readiness_summary,
    compute_steps_summary,
)
from app.services.sleep_service import (
    delete_sleep_log_by_date,
    list_sleep_logs,
    upsert_sleep_batch,
    upsert_sleep_log,
)
from app.services.steps_service import (
    delete_steps_log_by_date,
    list_steps_logs,
    upsert_steps_batch,
    upsert_steps_log,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Inicializa as tabelas do banco no startup da API."""
    init_db()
    yield


app = FastAPI(
    title="Monitor de Desempenho & Sono - API v1",
    description="Webhook HTTP e API REST para coleta de sono e recuperação (Samsung Health / Health Connect).",
    version="1.0.0",
    lifespan=lifespan,
)


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Valida X-API-Key quando WEBHOOK_API_KEY estiver configurada no ambiente."""
    expected_key = get_settings().WEBHOOK_API_KEY
    if not expected_key:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-API-Key.",
        )


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redireciona a raiz da API para a documentação interativa (/docs)."""
    return RedirectResponse(url="/docs")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Verifica disponibilidade da API."""
    return {"status": "ok", "version": "1.0.0", "module": "sleep_v1"}


@app.post(
    "/api/v1/sleep",
    response_model=SleepOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def ingest_sleep(
    payload: SleepIn,
    db: Session = Depends(get_db),
) -> SleepOut:
    """Recebe dados de uma noite de sono (celular/webhook) e faz upsert por sleep_date."""
    record = upsert_sleep_log(db, payload, raw_json=payload.model_dump_json())
    return SleepOut.model_validate(record)


@app.post(
    "/api/v1/sleep/batch",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def ingest_sleep_batch(
    payload: SleepBatchIn,
    db: Session = Depends(get_db),
) -> dict:
    """Importa um lote de registros de sono em transação única."""
    saved = upsert_sleep_batch(db, payload.records)
    return {
        "imported": len(saved),
        "records": [SleepOut.model_validate(item) for item in saved],
    }


@app.get(
    "/api/v1/sleep",
    response_model=list[SleepOut],
    dependencies=[Depends(verify_api_key)],
)
def get_sleep_history(
    limit: int = Query(default=90, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[SleepOut]:
    """Lista os registros recentes de sono ordenados da data mais recente para a mais antiga."""
    records = list_sleep_logs(db, limit=limit)
    return [SleepOut.model_validate(item) for item in records]


@app.get(
    "/api/v1/sleep/readiness",
    dependencies=[Depends(verify_api_key)],
)
def get_sleep_readiness(
    limit: int = Query(default=90, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Retorna o índice de prontidão diária, dívida de sono 7d e tendências EWMA."""
    target_hours = get_settings().SLEEP_TARGET_HOURS
    records = list_sleep_logs(db, limit=limit)
    df = build_sleep_analytics_df(records, target_hours=target_hours)
    return compute_readiness_summary(df, target_hours=target_hours)


@app.delete(
    "/api/v1/sleep/{sleep_date}",
    dependencies=[Depends(verify_api_key)],
)
def delete_sleep(
    sleep_date: date,
    db: Session = Depends(get_db),
) -> dict:
    """Remove o registro de sono de uma data específica."""
    deleted = delete_sleep_log_by_date(db, sleep_date)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nenhum registro encontrado para {sleep_date}.",
        )
    return {"deleted": True, "sleep_date": str(sleep_date)}


@app.post(
    "/api/v1/steps",
    response_model=StepsOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def ingest_steps(
    payload: StepsIn,
    db: Session = Depends(get_db),
) -> StepsOut:
    """Recebe contagem diária de passos (Android/webhook) e faz upsert por step_date."""
    record = upsert_steps_log(db, payload, raw_json=payload.model_dump_json())
    return StepsOut.model_validate(record)


@app.post(
    "/api/v1/steps/quick",
    response_model=StepsOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def ingest_steps_quick(
    steps: int = Query(..., gt=0, le=250000),
    step_date: date | None = Query(default=None),
    distance_km: float | None = Query(default=None, ge=0, le=300),
    notes: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> StepsOut:
    """Endpoint simplificado via query string para envio rápido de passos pelo celular."""
    payload = StepsIn(
        steps=steps,
        step_date=step_date,
        distance_km=distance_km,
        notes=notes,
        source="mobile_quick_url",
    )
    record = upsert_steps_log(db, payload, raw_json=payload.model_dump_json())
    return StepsOut.model_validate(record)


@app.post(
    "/api/v1/steps/batch",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_api_key)],
)
def ingest_steps_batch(
    payload: StepsBatchIn,
    db: Session = Depends(get_db),
) -> dict:
    """Importa um lote de registros de passos em transação única."""
    saved = upsert_steps_batch(db, payload.records)
    return {
        "imported": len(saved),
        "records": [StepsOut.model_validate(item) for item in saved],
    }


@app.get(
    "/api/v1/steps",
    response_model=list[StepsOut],
    dependencies=[Depends(verify_api_key)],
)
def get_steps_history(
    limit: int = Query(default=90, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[StepsOut]:
    """Lista registros diários de passos do mais recente para o mais antigo."""
    records = list_steps_logs(db, limit=limit)
    return [StepsOut.model_validate(item) for item in records]


@app.get(
    "/api/v1/steps/summary",
    dependencies=[Depends(verify_api_key)],
)
def get_steps_summary(
    limit: int = Query(default=90, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Retorna o resumo de passos com EWMA 7d/28d e ACWR."""
    target_steps = get_settings().STEPS_TARGET
    records = list_steps_logs(db, limit=limit)
    df = build_steps_analytics_df(records, target_steps=target_steps)
    return compute_steps_summary(df, target_steps=target_steps)


@app.get(
    "/api/v1/analytics/steps-sleep",
    dependencies=[Depends(verify_api_key)],
)
def get_steps_sleep_relation(
    limit: int = Query(default=90, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Retorna o cruzamento estatístico e série temporal entre passos diários e sono."""
    cfg = get_settings()
    steps_records = list_steps_logs(db, limit=limit)
    sleep_records = list_sleep_logs(db, limit=limit)
    relation = build_steps_sleep_relation(
        steps_records,
        sleep_records,
        target_steps=cfg.STEPS_TARGET,
        target_sleep_hours=cfg.SLEEP_TARGET_HOURS,
    )
    rel_df = relation.pop("df")
    if not rel_df.empty:
        clean_df = rel_df.astype(object).where(pd.notna(rel_df), None)
        records_out = clean_df.to_dict(orient="records")
        for row in records_out:
            if row.get("date") is not None:
                row["date"] = str(row["date"])
    else:
        records_out = []

    relation["records"] = records_out
    return relation


@app.delete(
    "/api/v1/steps/{step_date}",
    dependencies=[Depends(verify_api_key)],
)
def delete_steps(
    step_date: date,
    db: Session = Depends(get_db),
) -> dict:
    """Remove o registro de passos de uma data específica."""
    deleted = delete_steps_log_by_date(db, step_date)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nenhum registro de passos encontrado para {step_date}.",
        )
    return {"deleted": True, "step_date": str(step_date)}
