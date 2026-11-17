"""Testes automatizados para a pipeline v1 de Sono & Recuperação."""

from datetime import date
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api as api_module
from app import config as config_module
from app.database import Base, get_db
from app.services.analytics import (
    build_sleep_analytics_df,
    build_steps_analytics_df,
    compute_readiness_summary,
)
from app.services.importers import (
    generate_sample_sleep_data,
    generate_sample_steps_data,
    parse_samsung_health_or_generic_csv,
    parse_samsung_health_or_generic_steps_csv,
)
from app.services.sleep_service import upsert_sleep_batch
from app.services.steps_service import upsert_steps_batch


@pytest.fixture()
def test_client(monkeypatch):
    """Cria TestClient com banco SQLite isolado em memória por teste."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(api_module, "init_db", lambda: Base.metadata.create_all(bind=test_engine))
    monkeypatch.setenv("WEBHOOK_API_KEY", "")
    monkeypatch.setattr(config_module, "settings", config_module.get_settings())
    api_module.app.dependency_overrides[get_db] = override_get_db

    with TestClient(api_module.app) as client:
        yield client, TestingSessionLocal

    api_module.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def test_health_endpoint(test_client):
    client, _ = test_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "1.0.0", "module": "sleep_v1"}


def test_post_sleep_full_payload_and_upsert(test_client):
    client, _ = test_client
    payload = {
        "sleep_date": "2026-11-15",
        "bedtime": "2026-11-14T23:00:00",
        "wake_time": "2026-11-15T06:45:00",
        "duration_minutes": 440,
        "deep_sleep_minutes": 95,
        "light_sleep_minutes": 240,
        "rem_sleep_minutes": 105,
        "awake_minutes": 25,
        "resting_hr": 49,
        "hrv_rmssd": 72,
        "spo2_avg": 97.4,
        "source": "health_connect",
    }

    resp = client.post("/api/v1/sleep", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sleep_date"] == "2026-11-15"
    assert body["duration_minutes"] == 440
    assert body["duration_hours"] == 7.33
    assert body["time_in_bed_minutes"] == 465.0
    assert body["sleep_efficiency"] == pytest.approx(94.6, abs=0.2)
    assert 0 <= body["sleep_score"] <= 100

    # Upsert na mesma sleep_date deve atualizar sem duplicar
    updated_payload = dict(payload, duration_minutes=480, sleep_score=91.0, notes="Atualizado")
    resp2 = client.post("/api/v1/sleep", json=updated_payload)
    assert resp2.status_code == 200
    assert resp2.json()["duration_minutes"] == 480
    assert resp2.json()["sleep_score"] == 91.0

    list_resp = client.get("/api/v1/sleep")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_post_sleep_minimal_bedtime_and_wake_time(test_client):
    client, _ = test_client
    payload = {
        "bedtime": "2026-11-15T22:30:00-03:00",
        "wake_time": "2026-11-16T06:30:00-03:00",
        "source": "webhook",
    }

    resp = client.post("/api/v1/sleep", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_date"] == "2026-11-16"
    assert data["duration_minutes"] == 480.0
    assert data["duration_hours"] == 8.0
    assert data["sleep_efficiency"] == 100.0
    assert data["sleep_score"] is not None


def test_api_key_protection(test_client, monkeypatch):
    client, _ = test_client
    monkeypatch.setenv("WEBHOOK_API_KEY", "segredo-corrida")

    unauth = client.get("/api/v1/sleep")
    assert unauth.status_code == 401

    wrong = client.get("/api/v1/sleep", headers={"X-API-Key": "errado"})
    assert wrong.status_code == 401

    ok = client.get("/api/v1/sleep", headers={"X-API-Key": "segredo-corrida"})
    assert ok.status_code == 200


def test_readiness_and_ewma_analytics(test_client):
    client, SessionCls = test_client
    sample = generate_sample_sleep_data(days=14)
    with SessionCls() as db:
        saved = upsert_sleep_batch(db, sample)
        df = build_sleep_analytics_df(saved, target_hours=8.0)

    assert len(df) == 14
    assert "sleep_ewma_7d" in df.columns
    assert "sleep_ewma_28d" in df.columns
    assert "rolling_debt_7d" in df.columns
    assert (df["rolling_debt_7d"] >= 0).all()

    summary = compute_readiness_summary(df, target_hours=8.0)
    assert summary["has_data"] is True
    assert 0 <= summary["readiness_score"] <= 100

    resp = client.get("/api/v1/sleep/readiness")
    assert resp.status_code == 200
    api_summary = resp.json()
    assert api_summary["has_data"] is True
    assert api_summary["sleep_ewma_7d"] is not None
    assert api_summary["sleep_ewma_28d"] is not None


def test_parse_samsung_health_and_generic_csv():
    samsung_csv = (
        "com.samsung.shealth.sleep,2026111600,1\n"
        "start_time,end_time,sleep_score,efficiency,total_deep_duration,total_light_duration,total_rem_duration\n"
        "2026-11-14 23:00:00,2026-11-15 06:30:00,85,92,90,230,100\n"
        "2026-11-15 23:15:00,2026-11-16 07:00:00,88,94,5400000,14400000,6600000\n"
    )
    samsung_records = parse_samsung_health_or_generic_csv(samsung_csv)
    assert len(samsung_records) == 2
    assert samsung_records[0].source == "samsung_health"
    assert samsung_records[0].sleep_date == date(2026, 11, 15)
    assert samsung_records[0].deep_sleep_minutes == 90.0
    # Linha 2 com durações em milissegundos convertidas automaticamente para minutos
    assert samsung_records[1].deep_sleep_minutes == 90.0
    assert samsung_records[1].light_sleep_minutes == 240.0
    assert samsung_records[1].rem_sleep_minutes == 110.0

    generic_csv = (
        "data;horas_dormidas;profundo;rem;leve;fc_repouso;hrv\n"
        "2026-11-16;7,5;85;100;265;50;68\n"
    )
    generic_records = parse_samsung_health_or_generic_csv(generic_csv)
    assert len(generic_records) == 1
    assert generic_records[0].source == "csv_import"
    assert generic_records[0].duration_minutes == 450.0
    assert generic_records[0].resting_hr == 50.0
    assert generic_records[0].hrv_rmssd == 68.0


def test_post_steps_minimal_and_upsert(test_client):
    client, _ = test_client
    resp = client.post("/api/v1/steps", json={"steps": 9450})
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps_count"] == 9450
    assert body["goal_steps"] == 10000
    assert body["goal_pct"] == 94.5
    assert body["distance_km"] == pytest.approx(7.18, abs=0.02)
    assert body["calories_kcal"] == pytest.approx(378.0, abs=0.5)

    # Upsert na mesma data deve atualizar sem duplicar
    resp2 = client.post(
        "/api/v1/steps",
        json={"step_date": body["step_date"], "steps_count": 12000, "notes": "Atualizado"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["steps_count"] == 12000

    list_resp = client.get("/api/v1/steps")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_post_steps_quick_url(test_client):
    client, _ = test_client
    resp = client.post("/api/v1/steps/quick?steps=11200&step_date=2026-11-16")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_date"] == "2026-11-16"
    assert data["steps_count"] == 11200
    assert data["source"] == "mobile_quick_url"
    assert data["goal_pct"] == 112.0


def test_steps_summary_and_acwr(test_client):
    client, SessionCls = test_client
    sample_steps = generate_sample_steps_data(days=20)
    with SessionCls() as db:
        saved = upsert_steps_batch(db, sample_steps)
        df = build_steps_analytics_df(saved, target_steps=10000)

    assert len(df) == 20
    assert "steps_ewma_7d" in df.columns
    assert "steps_ewma_28d" in df.columns
    assert "steps_acwr" in df.columns

    resp = client.get("/api/v1/steps/summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["has_data"] is True
    assert summary["steps_ewma_7d"] > 0
    assert summary["steps_ewma_28d"] > 0
    assert summary["steps_acwr"] > 0


def test_steps_sleep_relation_endpoint(test_client):
    client, SessionCls = test_client
    sample_steps = generate_sample_steps_data(days=14)
    sample_sleep = generate_sample_sleep_data(days=14)
    with SessionCls() as db:
        upsert_steps_batch(db, sample_steps)
        upsert_sleep_batch(db, sample_sleep)

    resp = client.get("/api/v1/analytics/steps-sleep")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_steps"] is True
    assert data["has_sleep"] is True
    assert data["paired_morning_days"] == 14
    assert data["paired_next_days"] == 13
    assert len(data["records"]) == 14
    assert isinstance(data["insights"], list) and len(data["insights"]) > 0


def test_parse_samsung_health_and_generic_steps_csv():
    # 2026-11-16 00:00:00 UTC em milissegundos = 1794787200000
    samsung_steps_csv = (
        "com.samsung.shealth.step_daily_trend,2026111600,1\n"
        "day_time,count,distance,calorie,active_time\n"
        "1794700800000,8200,6250,310,3600000\n"
        "1794787200000,4100,3100,155,1800000\n"
        "1794787200000,10450,8100,420,4800000\n"
    )
    samsung_records = parse_samsung_health_or_generic_steps_csv(samsung_steps_csv)
    assert len(samsung_records) == 2
    assert samsung_records[0].source == "samsung_health"
    assert samsung_records[0].step_date == date(2026, 11, 15)
    assert samsung_records[0].steps_count == 8200
    assert samsung_records[0].distance_km == 6.25
    assert samsung_records[0].active_minutes == 60.0
    # Na segunda data deve manter a linha consolidada com maior contagem (10450)
    assert samsung_records[1].step_date == date(2026, 11, 16)
    assert samsung_records[1].steps_count == 10450
    assert samsung_records[1].distance_km == 8.1
    assert samsung_records[1].active_minutes == 80.0

    generic_csv = (
        "data;passos;distancia;minutos_ativos;calorias\n"
        "2026-11-16;9800;7,45;75;392\n"
    )
    generic_records = parse_samsung_health_or_generic_steps_csv(generic_csv)
    assert len(generic_records) == 1
    assert generic_records[0].source == "csv_import"
    assert generic_records[0].steps_count == 9800
    assert generic_records[0].distance_km == 7.45
    assert generic_records[0].active_minutes == 75.0
