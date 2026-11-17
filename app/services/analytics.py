"""Modelagem de séries temporais (EWMA, baselines, passos e relação passos × sono)."""

from datetime import timedelta
import numpy as np
import pandas as pd

from app.models import DailyStepsLog, SleepLog

ANALYTICS_COLUMNS = [
    "sleep_date",
    "duration_minutes",
    "duration_hours",
    "time_in_bed_minutes",
    "deep_sleep_minutes",
    "light_sleep_minutes",
    "rem_sleep_minutes",
    "awake_minutes",
    "sleep_efficiency",
    "sleep_score",
    "resting_hr",
    "hrv_rmssd",
    "spo2_avg",
    "respiratory_rate",
    "skin_temp_delta",
    "subjective_feeling",
    "source",
    "notes",
    "daily_sleep_debt",
    "rolling_debt_7d",
    "sleep_ewma_7d",
    "sleep_ewma_28d",
    "sleep_consistency_ratio",
    "hrv_ewma_7d",
    "hrv_mean_28d",
    "hrv_std_28d",
    "rhr_ewma_7d",
    "rhr_mean_28d",
    "rhr_std_28d",
]


def build_sleep_analytics_df(logs: list[SleepLog], target_hours: float = 8.0) -> pd.DataFrame:
    """Constrói DataFrame temporal com dívida de sono, EWMA 7d/28d e baselines de HRV/FC."""
    if not logs:
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    rows = [
        {
            "sleep_date": log.sleep_date,
            "duration_minutes": log.duration_minutes,
            "time_in_bed_minutes": log.time_in_bed_minutes,
            "deep_sleep_minutes": log.deep_sleep_minutes,
            "light_sleep_minutes": log.light_sleep_minutes,
            "rem_sleep_minutes": log.rem_sleep_minutes,
            "awake_minutes": log.awake_minutes,
            "sleep_efficiency": log.sleep_efficiency,
            "sleep_score": log.sleep_score,
            "resting_hr": log.resting_hr,
            "hrv_rmssd": log.hrv_rmssd,
            "spo2_avg": log.spo2_avg,
            "respiratory_rate": log.respiratory_rate,
            "skin_temp_delta": log.skin_temp_delta,
            "subjective_feeling": log.subjective_feeling,
            "source": log.source,
            "notes": log.notes,
        }
        for log in logs
    ]

    df = pd.DataFrame(rows)
    df["sleep_date_dt"] = pd.to_datetime(df["sleep_date"])
    df = df.sort_values("sleep_date_dt").reset_index(drop=True)

    df["duration_hours"] = (df["duration_minutes"] / 60.0).round(2)
    net_deficit = target_hours - df["duration_hours"]
    df["daily_sleep_debt"] = net_deficit.clip(lower=0.0).round(2)

    dt_indexed_deficit = pd.Series(net_deficit.to_numpy(), index=df["sleep_date_dt"])
    df["rolling_debt_7d"] = (
        dt_indexed_deficit.rolling("7D", min_periods=1).sum().clip(lower=0.0).round(2).to_numpy()
    )

    df["sleep_ewma_7d"] = df["duration_hours"].ewm(span=7, adjust=False).mean().round(2)
    df["sleep_ewma_28d"] = df["duration_hours"].ewm(span=28, adjust=False).mean().round(2)
    df["sleep_consistency_ratio"] = (
        df["sleep_ewma_7d"] / df["sleep_ewma_28d"].replace(0, np.nan)
    ).round(3)

    # Baselines autonômicas (HRV e FC de repouso)
    for col, prefix in (("hrv_rmssd", "hrv"), ("resting_hr", "rhr")):
        series = pd.to_numeric(df[col], errors="coerce")
        df[f"{prefix}_ewma_7d"] = series.ewm(span=7, adjust=False, ignore_na=True).mean().round(1)
        df[f"{prefix}_mean_28d"] = series.rolling(window=28, min_periods=1).mean().round(1)
        df[f"{prefix}_std_28d"] = (
            series.rolling(window=28, min_periods=1).std(ddof=0).fillna(0.0).round(1)
        )

    df = df.drop(columns=["sleep_date_dt"])
    return df


# Alias para compatibilidade com nomenclatura de referência
build_sleep_Analytics_df = build_sleep_analytics_df


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _evaluate_autonomic_markers(latest: pd.Series) -> tuple[str, float, str, float, list[str]]:
    """Avalia desvios de HRV e FC de repouso em relação à baseline de 28 dias."""
    insights: list[str] = []

    hrv = latest.get("hrv_rmssd")
    hrv_base = latest.get("hrv_mean_28d")
    hrv_std = latest.get("hrv_std_28d")
    hrv_component = 75.0
    hrv_status = "Sem dados de HRV"

    if pd.notna(hrv) and pd.notna(hrv_base) and hrv_base > 0:
        pct_diff = ((float(hrv) - float(hrv_base)) / float(hrv_base)) * 100.0
        std_val = float(hrv_std) if pd.notna(hrv_std) else 0.0
        if pct_diff >= 5.0:
            hrv_status = f"Acima da baseline (+{pct_diff:.1f}%)"
            hrv_component = _clamp(85.0 + min(15.0, pct_diff))
            insights.append(
                f"HRV em {hrv:.0f} ms (+{pct_diff:.1f}% vs média 28d): boa resposta parassimpática."
            )
        elif pct_diff <= -10.0 or (std_val > 0 and float(hrv) < float(hrv_base) - std_val):
            hrv_status = f"Abaixo da baseline ({pct_diff:.1f}%)"
            hrv_component = _clamp(70.0 + pct_diff * 1.8, 25.0, 75.0)
            insights.append(
                f"HRV suprimida em {hrv:.0f} ms ({pct_diff:.1f}% vs média 28d): sinal de estresse/fadiga."
            )
        else:
            hrv_status = f"Dentro da baseline ({pct_diff:+.1f}%)"
            hrv_component = 78.0

    rhr = latest.get("resting_hr")
    rhr_base = latest.get("rhr_mean_28d")
    rhr_component = 75.0
    rhr_status = "Sem dados de FC repouso"

    if pd.notna(rhr) and pd.notna(rhr_base) and rhr_base > 0:
        diff_bpm = float(rhr) - float(rhr_base)
        if diff_bpm >= 4.0:
            rhr_status = f"Elevada (+{diff_bpm:.1f} bpm vs baseline)"
            rhr_component = _clamp(75.0 - diff_bpm * 6.5, 20.0, 70.0)
            insights.append(
                f"FC de repouso elevada ({rhr:.0f} bpm, +{diff_bpm:.1f} bpm vs 28d): atenção à fadiga."
            )
        elif diff_bpm <= -2.0:
            rhr_status = f"Baixa / Recuperada ({diff_bpm:.1f} bpm)"
            rhr_component = 92.0
        else:
            rhr_status = f"Normal ({diff_bpm:+.1f} bpm)"
            rhr_component = 80.0

    return hrv_status, hrv_component, rhr_status, rhr_component, insights


def compute_readiness_summary(df: pd.DataFrame, target_hours: float = 8.0) -> dict:
    """Calcula o resumo diário de prontidão, dívida de sono e recomendações."""
    if df.empty:
        return {
            "has_data": False,
            "latest_date": None,
            "readiness_score": None,
            "status_label": "⚪ Sem dados de sono ainda",
            "status_color": "gray",
            "sleep_debt_7d_hours": 0.0,
            "last_duration_hours": None,
            "last_sleep_score": None,
            "sleep_ewma_7d": None,
            "sleep_ewma_28d": None,
            "sleep_consistency_ratio": None,
            "hrv_status": "Sem dados de HRV",
            "rhr_status": "Sem dados de FC repouso",
            "insights": [
                "Registre sua primeira noite de sono, importe um CSV do Samsung Health ou carregue os dados de exemplo."
            ],
        }

    latest = df.iloc[-1]
    last_hours = float(latest["duration_hours"])
    last_score = (
        float(latest["sleep_score"])
        if pd.notna(latest["sleep_score"])
        else min(100.0, (last_hours / max(target_hours, 1.0)) * 90.0)
    )
    debt_7d = float(latest["rolling_debt_7d"])
    ewma_7d = float(latest["sleep_ewma_7d"])
    ewma_28d = float(latest["sleep_ewma_28d"])
    ratio = (
        float(latest["sleep_consistency_ratio"])
        if pd.notna(latest["sleep_consistency_ratio"])
        else 1.0
    )

    # 1. Componente da última noite (0-100)
    night_component = _clamp(last_score)

    # 2. Componente de consistência 7d vs 28d e dívida acumulada (0-100)
    ratio_score = _clamp(100.0 - max(0.0, (1.0 - ratio) * 220.0))
    debt_penalty = min(45.0, debt_7d * 5.5)
    consistency_component = _clamp(ratio_score - debt_penalty)

    # 3. Marcadores autonômicos
    hrv_status, hrv_component, rhr_status, rhr_component, insights = _evaluate_autonomic_markers(
        latest
    )

    readiness_score = (
        0.45 * night_component
        + 0.25 * consistency_component
        + 0.15 * hrv_component
        + 0.15 * rhr_component
    )

    feeling = latest.get("subjective_feeling")
    if pd.notna(feeling):
        readiness_score += (float(feeling) - 3.0) * 3.0

    readiness_score = round(_clamp(readiness_score), 1)

    if readiness_score >= 75.0:
        status_label = "🟢 Pronto para Treino Intenso / Longo"
        status_color = "green"
    elif readiness_score >= 55.0:
        status_label = "🟡 Recuperação Moderada — Preferir Rodagem Leve/Moderada"
        status_color = "orange"
    else:
        status_label = "🔴 Alerta de Fadiga / Dívida de Sono — Priorizar Recuperação"
        status_color = "red"

    # Insights de duração, dívida e arquitetura do sono
    if last_hours < target_hours - 1.0:
        insights.append(
            f"Última noite abaixo da meta ({last_hours:.1f}h vs meta de {target_hours:.1f}h)."
        )
    elif last_hours >= target_hours:
        insights.append(
            f"Meta diária atingida na última noite ({last_hours:.1f}h dormidas)."
        )

    if debt_7d >= 4.0:
        insights.append(
            f"Dívida de sono acumulada em {debt_7d:.1f}h nos últimos 7 dias: considere dormir mais cedo hoje."
        )
    elif debt_7d <= 1.0:
        insights.append(f"Dívida de sono controlada nos últimos 7 dias ({debt_7d:.1f}h).")

    deep = latest.get("deep_sleep_minutes")
    rem = latest.get("rem_sleep_minutes")
    dur_min = float(latest["duration_minutes"])
    if (pd.notna(deep) or pd.notna(rem)) and dur_min > 0:
        restorative_pct = ((float(deep or 0.0) + float(rem or 0.0)) / dur_min) * 100.0
        if restorative_pct < 30.0:
            insights.append(
                f"Sono restaurador (Profundo + REM) baixo: {restorative_pct:.0f}% do total (ideal ~35-45%)."
            )
        else:
            insights.append(
                f"Boa proporção de sono restaurador (Profundo + REM): {restorative_pct:.0f}%."
            )

    if ratio < 0.90:
        insights.append(
            f"Média aguda 7d ({ewma_7d:.1f}h) está abaixo da crônica 28d ({ewma_28d:.1f}h), razão {ratio:.2f}."
        )

    return {
        "has_data": True,
        "latest_date": str(latest["sleep_date"]),
        "readiness_score": readiness_score,
        "status_label": status_label,
        "status_color": status_color,
        "sleep_debt_7d_hours": round(debt_7d, 2),
        "last_duration_hours": round(last_hours, 2),
        "last_sleep_score": round(last_score, 1),
        "sleep_ewma_7d": round(ewma_7d, 2),
        "sleep_ewma_28d": round(ewma_28d, 2),
        "sleep_consistency_ratio": round(ratio, 3),
        "hrv_status": hrv_status,
        "rhr_status": rhr_status,
        "insights": insights,
    }


STEPS_ANALYTICS_COLUMNS = [
    "step_date",
    "steps_count",
    "goal_steps",
    "goal_pct",
    "distance_km",
    "active_minutes",
    "calories_kcal",
    "floors_climbed",
    "source",
    "notes",
    "steps_ewma_7d",
    "steps_ewma_28d",
    "steps_acwr",
    "rolling_steps_7d",
    "rolling_km_7d",
]


def build_steps_analytics_df(logs: list[DailyStepsLog], target_steps: int = 10000) -> pd.DataFrame:
    """Constrói DataFrame temporal de passos com EWMA 7d/28d, ACWR e acumulados 7d."""
    if not logs:
        return pd.DataFrame(columns=STEPS_ANALYTICS_COLUMNS)

    rows = [
        {
            "step_date": log.step_date,
            "steps_count": log.steps_count,
            "goal_steps": log.goal_steps or target_steps,
            "distance_km": log.distance_km,
            "active_minutes": log.active_minutes,
            "calories_kcal": log.calories_kcal,
            "floors_climbed": log.floors_climbed,
            "source": log.source,
            "notes": log.notes,
        }
        for log in logs
    ]

    df = pd.DataFrame(rows)
    df["step_date_dt"] = pd.to_datetime(df["step_date"])
    df = df.sort_values("step_date_dt").reset_index(drop=True)

    df["goal_pct"] = ((df["steps_count"] / max(target_steps, 1)) * 100.0).round(1)
    df["steps_ewma_7d"] = df["steps_count"].ewm(span=7, adjust=False).mean().round(0)
    df["steps_ewma_28d"] = df["steps_count"].ewm(span=28, adjust=False).mean().round(0)
    df["steps_acwr"] = (
        df["steps_ewma_7d"] / df["steps_ewma_28d"].replace(0, np.nan)
    ).round(2)

    dt_steps = pd.Series(df["steps_count"].to_numpy(), index=df["step_date_dt"])
    dt_km = pd.Series(
        pd.to_numeric(df["distance_km"], errors="coerce").fillna(0.0).to_numpy(),
        index=df["step_date_dt"],
    )
    df["rolling_steps_7d"] = dt_steps.rolling("7D", min_periods=1).sum().round(0).astype(int).to_numpy()
    df["rolling_km_7d"] = dt_km.rolling("7D", min_periods=1).sum().round(2).to_numpy()

    df = df.drop(columns=["step_date_dt"])
    return df


def compute_steps_summary(df: pd.DataFrame, target_steps: int = 10000) -> dict:
    """Resume carga ambulatorial de passos (EWMA 7d/28d e ACWR)."""
    if df.empty:
        return {
            "has_data": False,
            "latest_date": None,
            "last_steps": None,
            "goal_pct": None,
            "last_distance_km": None,
            "steps_ewma_7d": None,
            "steps_ewma_28d": None,
            "steps_acwr": None,
            "rolling_steps_7d": 0,
            "rolling_km_7d": 0.0,
            "status_label": "⚪ Sem dados de passos ainda",
            "status_color": "gray",
            "insights": [
                "Envie os passos reais do seu celular Android, importe o CSV do Samsung Health ou lance manualmente."
            ],
        }

    latest = df.iloc[-1]
    last_steps = int(latest["steps_count"])
    goal_pct = float(latest["goal_pct"])
    last_distance_km = float(latest["distance_km"]) if pd.notna(latest["distance_km"]) else 0.0
    ewma_7d = float(latest["steps_ewma_7d"])
    ewma_28d = float(latest["steps_ewma_28d"])
    acwr = float(latest["steps_acwr"]) if pd.notna(latest["steps_acwr"]) else 1.0
    rolling_steps_7d = int(latest["rolling_steps_7d"])
    rolling_km_7d = float(latest["rolling_km_7d"])

    if acwr > 1.30:
        status_label = "🟡 Pico Agudo de Volume de Passos (Atenção à Fadiga)"
        status_color = "orange"
    elif acwr < 0.80:
        status_label = "🔵 Volume Reduzido / Recuperação"
        status_color = "blue"
    else:
        status_label = "🟢 Carga de Passos Equilibrada (Sweet Spot)"
        status_color = "green"

    insights: list[str] = []
    if last_steps >= target_steps:
        insights.append(
            f"Meta diária superada no último registro: {last_steps:,} passos ({goal_pct:.0f}% de {target_steps:,}).".replace(",", ".")
        )
    else:
        insights.append(
            f"Último registro com {last_steps:,} passos ({goal_pct:.0f}% da meta de {target_steps:,}).".replace(",", ".")
        )

    insights.append(
        f"Acumulado nos últimos 7 dias: {rolling_steps_7d:,} passos (~{rolling_km_7d:.1f} km).".replace(",", ".")
    )
    insights.append(
        f"Tendência aguda (EWMA 7d): {ewma_7d:,.0f} passos/dia vs crônica (EWMA 28d): {ewma_28d:,.0f} passos/dia (ACWR {acwr:.2f}).".replace(",", ".")
    )

    return {
        "has_data": True,
        "latest_date": str(latest["step_date"]),
        "last_steps": last_steps,
        "goal_pct": round(goal_pct, 1),
        "last_distance_km": round(last_distance_km, 2),
        "steps_ewma_7d": round(ewma_7d, 0),
        "steps_ewma_28d": round(ewma_28d, 0),
        "steps_acwr": round(acwr, 2),
        "rolling_steps_7d": rolling_steps_7d,
        "rolling_km_7d": round(rolling_km_7d, 2),
        "status_label": status_label,
        "status_color": status_color,
        "insights": insights,
    }


def _safe_correlation(series_a: pd.Series, series_b: pd.Series) -> float | None:
    """Calcula correlação de Pearson apenas quando há pares suficientes e variância."""
    paired = pd.DataFrame({"a": series_a, "b": series_b}).dropna()
    if len(paired) < 3:
        return None
    if paired["a"].nunique() < 2 or paired["b"].nunique() < 2:
        return None
    corr = paired["a"].corr(paired["b"])
    if pd.isna(corr):
        return None
    return round(float(corr), 3)


def build_steps_sleep_relation(
    steps_logs: list[DailyStepsLog],
    sleep_logs: list[SleepLog],
    target_steps: int = 10000,
    target_sleep_hours: float = 8.0,
) -> dict:
    """Cruza passos diários com o sono da manhã do dia e da noite seguinte."""
    steps_df = build_steps_analytics_df(steps_logs, target_steps=target_steps)
    sleep_df = build_sleep_analytics_df(sleep_logs, target_hours=target_sleep_hours)

    has_steps = not steps_df.empty
    has_sleep = not sleep_df.empty

    base_columns = [
        "date",
        "steps_count",
        "distance_km",
        "goal_pct",
        "steps_ewma_7d",
        "steps_acwr",
        "morning_sleep_hours",
        "morning_sleep_score",
        "morning_hrv",
        "morning_rhr",
        "next_sleep_hours",
        "next_deep_minutes",
        "next_sleep_score",
        "next_hrv",
        "next_rhr",
    ]

    if not has_steps and not has_sleep:
        return {
            "df": pd.DataFrame(columns=base_columns),
            "has_steps": False,
            "has_sleep": False,
            "paired_morning_days": 0,
            "paired_next_days": 0,
            "corr_sleep_to_steps": None,
            "corr_steps_to_next_deep": None,
            "corr_steps_to_next_hrv": None,
            "corr_steps_to_next_rhr": None,
            "insights": [
                "Registre passos diários do celular e dados de sono para visualizar correlações entre atividade e recuperação."
            ],
        }

    if has_steps:
        steps_part = steps_df[
            ["step_date", "steps_count", "distance_km", "goal_pct", "steps_ewma_7d", "steps_acwr"]
        ].rename(columns={"step_date": "date"})
        steps_part["date"] = pd.to_datetime(steps_part["date"]).dt.date
    else:
        steps_part = pd.DataFrame(
            columns=["date", "steps_count", "distance_km", "goal_pct", "steps_ewma_7d", "steps_acwr"]
        )

    if has_sleep:
        sleep_base = sleep_df.copy()
        sleep_base["date"] = pd.to_datetime(sleep_base["sleep_date"]).dt.date

        morning_part = sleep_base[
            ["date", "duration_hours", "sleep_score", "hrv_rmssd", "resting_hr"]
        ].rename(
            columns={
                "duration_hours": "morning_sleep_hours",
                "sleep_score": "morning_sleep_score",
                "hrv_rmssd": "morning_hrv",
                "resting_hr": "morning_rhr",
            }
        )

        next_part = sleep_base[
            ["date", "duration_hours", "deep_sleep_minutes", "sleep_score", "hrv_rmssd", "resting_hr"]
        ].copy()
        next_part["date"] = next_part["date"].apply(lambda d: d - timedelta(days=1))
        next_part = next_part.rename(
            columns={
                "duration_hours": "next_sleep_hours",
                "deep_sleep_minutes": "next_deep_minutes",
                "sleep_score": "next_sleep_score",
                "hrv_rmssd": "next_hrv",
                "resting_hr": "next_rhr",
            }
        )
    else:
        morning_part = pd.DataFrame(
            columns=["date", "morning_sleep_hours", "morning_sleep_score", "morning_hrv", "morning_rhr"]
        )
        next_part = pd.DataFrame(
            columns=["date", "next_sleep_hours", "next_deep_minutes", "next_sleep_score", "next_hrv", "next_rhr"]
        )

    merged = pd.merge(steps_part, morning_part, on="date", how="outer")
    merged = pd.merge(merged, next_part, on="date", how="left")
    merged = merged.sort_values("date").reset_index(drop=True)

    paired_morning = int(merged[["steps_count", "morning_sleep_hours"]].dropna().shape[0])
    paired_next = int(merged[["steps_count", "next_sleep_hours"]].dropna().shape[0])

    corr_sleep_to_steps = _safe_correlation(merged["morning_sleep_hours"], merged["steps_count"])
    corr_steps_to_next_deep = _safe_correlation(merged["steps_count"], merged["next_deep_minutes"])
    corr_steps_to_next_hrv = _safe_correlation(merged["steps_count"], merged["next_hrv"])
    corr_steps_to_next_rhr = _safe_correlation(merged["steps_count"], merged["next_rhr"])

    insights: list[str] = []
    if has_steps and not has_sleep:
        insights.append(
            f"Você já tem {len(steps_df)} dia(s) de passos reais registrados. Assim que adicionar os dados de sono, a correlação será calculada automaticamente."
        )
    elif has_sleep and not has_steps:
        insights.append(
            f"Você já tem {len(sleep_df)} noite(s) de sono registradas. Envie seus passos diários do celular para cruzar os dados."
        )
    elif paired_morning == 0 and paired_next == 0:
        insights.append(
            "Existem registros de passos e sono, mas ainda sem datas em comum para cruzamento."
        )
    else:
        high_days = merged.dropna(subset=["steps_count", "next_sleep_hours"])
        if not high_days.empty:
            above = high_days[high_days["steps_count"] >= target_steps]
            below = high_days[high_days["steps_count"] < target_steps]
            if not above.empty and not below.empty:
                insights.append(
                    f"Em dias acima de {target_steps:,} passos, seu sono na noite seguinte teve média de {above['next_sleep_hours'].mean():.2f}h (vs {below['next_sleep_hours'].mean():.2f}h em dias abaixo da meta).".replace(",", ".")
                )
        if corr_steps_to_next_deep is not None and abs(corr_steps_to_next_deep) >= 0.3:
            direction = "positiva" if corr_steps_to_next_deep > 0 else "negativa"
            insights.append(
                f"Correlação {direction} entre passos do dia e sono profundo na noite seguinte (r = {corr_steps_to_next_deep:+.2f})."
            )
        if corr_sleep_to_steps is not None and abs(corr_sleep_to_steps) >= 0.3:
            direction = "positiva" if corr_sleep_to_steps > 0 else "negativa"
            insights.append(
                f"Correlação {direction} entre horas dormidas ao acordar e volume de passos no dia (r = {corr_sleep_to_steps:+.2f})."
            )
        if not insights:
            insights.append(
                f"Cruzamento ativo com {paired_morning} dia(s) pareados (manhã) e {paired_next} dia(s) com noite seguinte. Continue acumulando dias para identificar padrões."
            )

    return {
        "df": merged,
        "has_steps": has_steps,
        "has_sleep": has_sleep,
        "paired_morning_days": paired_morning,
        "paired_next_days": paired_next,
        "corr_sleep_to_steps": corr_sleep_to_steps,
        "corr_steps_to_next_deep": corr_steps_to_next_deep,
        "corr_steps_to_next_hrv": corr_steps_to_next_hrv,
        "corr_steps_to_next_rhr": corr_steps_to_next_rhr,
        "insights": insights,
    }
