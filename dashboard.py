"""Dashboard interativo em Streamlit para Passos Diários, Sono e Relação Passos × Sono."""

from datetime import date, datetime, time, timedelta
import json
import socket
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from app.config import settings
from app.database import SessionLocal, init_db
from app.schemas import SleepIn, StepsIn, StepsOut
from app.services.analytics import (
    build_sleep_analytics_df,
    build_steps_analytics_df,
    build_steps_sleep_relation,
    compute_readiness_summary,
    compute_steps_summary,
)
from app.services.importers import (
    generate_csv_template,
    generate_sample_sleep_data,
    generate_sample_steps_data,
    generate_steps_csv_template,
    parse_samsung_health_or_generic_csv,
    parse_samsung_health_or_generic_steps_csv,
    parse_sleep_json,
    parse_steps_json,
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

st.set_page_config(
    page_title="Monitor Fit — Passos & Sono",
    page_icon="🏃",
    layout="wide",
)

init_db()


def get_local_lan_ip() -> str:
    """Detecta o IP local da máquina na rede Wi-Fi/LAN para configurar o celular."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def load_all_analytics(
    target_steps: int,
    target_hours: float,
    limit: int = 180,
) -> tuple[pd.DataFrame, dict, pd.DataFrame, dict, dict]:
    """Carrega registros do banco e calcula analytics de passos, sono e cruzamento."""
    with SessionLocal() as db:
        steps_logs = list_steps_logs(db, limit=limit)
        sleep_logs = list_sleep_logs(db, limit=limit)

    steps_df = build_steps_analytics_df(steps_logs, target_steps=target_steps)
    steps_summary = compute_steps_summary(steps_df, target_steps=target_steps)

    sleep_df = build_sleep_analytics_df(sleep_logs, target_hours=target_hours)
    readiness = compute_readiness_summary(sleep_df, target_hours=target_hours)

    relation = build_steps_sleep_relation(
        steps_logs,
        sleep_logs,
        target_steps=target_steps,
        target_sleep_hours=target_hours,
    )
    return steps_df, steps_summary, sleep_df, readiness, relation


def save_sample_datasets(days: int = 30, include_sleep: bool = True) -> tuple[int, int]:
    """Popula o banco com 30 dias de exemplo (passos e opcionalmente sono)."""
    sample_steps = generate_sample_steps_data(days=days)
    with SessionLocal() as db:
        saved_steps = upsert_steps_batch(db, sample_steps)
        saved_sleep_count = 0
        if include_sleep:
            sample_sleep = generate_sample_sleep_data(days=days)
            saved_sleep = upsert_sleep_batch(db, sample_sleep)
            saved_sleep_count = len(saved_sleep)
    return len(saved_steps), saved_sleep_count


# --- Gráficos de Passos ---


def plot_steps_and_ewma(df: pd.DataFrame, target_steps: int) -> go.Figure:
    """Gráfico 1 de Passos: Barras diárias + Meta + EWMA 7d/28d."""
    fig = go.Figure()
    dates = pd.to_datetime(df["step_date"])
    bar_colors = [
        "#2E8B57" if s >= target_steps else ("#E69F00" if s >= target_steps * 0.7 else "#DC2626")
        for s in df["steps_count"]
    ]

    fig.add_trace(
        go.Bar(
            x=dates,
            y=df["steps_count"],
            name="Passos Diários",
            marker_color=bar_colors,
            opacity=0.82,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["steps_ewma_7d"],
            name="Carga Aguda (EWMA 7d)",
            mode="lines+markers",
            line=dict(color="#1D4ED8", width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["steps_ewma_28d"],
            name="Carga Crônica (EWMA 28d)",
            mode="lines",
            line=dict(color="#111827", width=2, dash="dot"),
        )
    )
    fig.add_hline(
        y=target_steps,
        line_dash="dash",
        line_color="#059669",
        annotation_text=f"Meta ({target_steps:,})".replace(",", "."),
        annotation_position="top left",
    )

    fig.update_layout(
        title="Passos Diários vs. Carga Aguda/Crônica (EWMA 7d / 28d)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        yaxis_title="Passos",
        margin=dict(l=20, r=20, t=60, b=20),
        height=390,
    )
    return fig


def plot_steps_acwr(df: pd.DataFrame) -> go.Figure:
    """Gráfico 2 de Passos: Curva ACWR com faixa verde da zona segura (0.80 a 1.30)."""
    fig = go.Figure()
    dates = pd.to_datetime(df["step_date"])

    fig.add_hrect(
        y0=0.80,
        y1=1.30,
        fillcolor="rgba(16, 185, 129, 0.14)",
        line_width=0,
        annotation_text="Zona Segura (0.80 - 1.30)",
        annotation_position="top left",
    )
    fig.add_hline(y=1.0, line_dash="dot", line_color="#6B7280")
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["steps_acwr"],
            name="ACWR Passos (7d / 28d)",
            mode="lines+markers",
            line=dict(color="#2563EB", width=3),
        )
    )

    fig.update_layout(
        title="Razão de Carga Aguda:Crônica (ACWR) de Passos",
        hovermode="x unified",
        yaxis_title="ACWR",
        yaxis=dict(rangemode="tozero"),
        margin=dict(l=20, r=20, t=55, b=20),
        height=320,
    )
    return fig


# --- Gráficos de Relação Passos × Sono ---


def plot_steps_vs_sleep_timeline(rel_df: pd.DataFrame) -> go.Figure:
    """Gráfico combinado de Passos do Dia vs. Sono da Noite Seguinte."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    dates = pd.to_datetime(rel_df["date"])

    fig.add_trace(
        go.Bar(
            x=dates,
            y=rel_df["steps_count"],
            name="Passos no Dia",
            marker_color="rgba(37, 99, 235, 0.65)",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=rel_df["next_sleep_hours"],
            name="Sono Noite Seguinte (h)",
            mode="lines+markers",
            line=dict(color="#7C3AED", width=3),
            connectgaps=False,
        ),
        secondary_y=True,
    )
    if rel_df["next_deep_minutes"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=(rel_df["next_deep_minutes"] / 60.0).round(2),
                name="Sono Profundo Noite Seguinte (h)",
                mode="lines+markers",
                line=dict(color="#059669", width=2, dash="dot"),
                connectgaps=False,
            ),
            secondary_y=True,
        )

    fig.update_layout(
        title="Passos do Dia × Recuperação na Noite Seguinte",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=20, r=20, t=60, b=20),
        height=400,
    )
    fig.update_yaxes(title_text="Passos", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Horas de Sono (h)", secondary_y=True, rangemode="tozero")
    return fig


def plot_steps_sleep_scatter(rel_df: pd.DataFrame, y_metric: str) -> go.Figure | None:
    """Gráfico de dispersão entre Passos no Dia e métrica de sono da noite seguinte."""
    metric_labels = {
        "next_sleep_hours": "Horas de Sono (Noite Seguinte)",
        "next_deep_minutes": "Sono Profundo (min — Noite Seguinte)",
        "next_hrv": "HRV RMSSD (ms — Noite Seguinte)",
    }
    valid = rel_df.dropna(subset=["steps_count", y_metric]).copy()
    if valid.empty:
        return None

    colors = valid["next_sleep_score"].fillna(75.0)
    fig = go.Figure(
        data=go.Scatter(
            x=valid["steps_count"],
            y=valid[y_metric],
            mode="markers",
            text=valid["date"].astype(str),
            marker=dict(
                size=11,
                color=colors,
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title="Score Sono"),
            ),
            hovertemplate="Data: %{text}<br>Passos: %{x:,}<br>"
            + metric_labels[y_metric]
            + ": %{y:.1f}<extra></extra>",
        )
    )
    fig.update_layout(
        title=f"Dispersão: Passos no Dia vs. {metric_labels[y_metric]}",
        xaxis_title="Passos no Dia",
        yaxis_title=metric_labels[y_metric],
        margin=dict(l=20, r=20, t=55, b=20),
        height=370,
    )
    return fig


# --- Gráficos de Sono ---


def plot_duration_and_ewma(df: pd.DataFrame, target_hours: float) -> go.Figure:
    """Gráfico 1 de Sono: Duração diária + Meta + EWMA 7d/28d + Dívida acumulada 7d."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    dates = pd.to_datetime(df["sleep_date"])
    bar_colors = [
        "#2E8B57" if h >= target_hours else ("#E69F00" if h >= target_hours - 1.0 else "#D55E00")
        for h in df["duration_hours"]
    ]

    fig.add_trace(
        go.Bar(
            x=dates,
            y=df["duration_hours"],
            name="Sono Diário (h)",
            marker_color=bar_colors,
            opacity=0.82,
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["sleep_ewma_7d"],
            name="Média Aguda (EWMA 7d)",
            mode="lines+markers",
            line=dict(color="#1F77B4", width=3),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["sleep_ewma_28d"],
            name="Baseline Crônica (EWMA 28d)",
            mode="lines",
            line=dict(color="#111827", width=2, dash="dot"),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=df["rolling_debt_7d"],
            name="Dívida Acumulada 7d (h)",
            mode="lines",
            line=dict(color="#B91C1C", width=2, dash="dash"),
            fill="tozeroy",
            fillcolor="rgba(185, 28, 28, 0.08)",
        ),
        secondary_y=True,
    )

    fig.add_hline(
        y=target_hours,
        line_dash="dash",
        line_color="#059669",
        annotation_text=f"Meta ({target_hours:.1f}h)",
        annotation_position="top left",
        secondary_y=False,
    )

    fig.update_layout(
        title="Duração do Sono, Tendência Aguda/Crônica (EWMA) e Dívida 7d",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=20, r=20, t=60, b=20),
        height=400,
    )
    fig.update_yaxes(title_text="Horas de Sono (h)", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Dívida 7d (h)", secondary_y=True, rangemode="tozero")
    return fig


def plot_sleep_architecture(df: pd.DataFrame) -> go.Figure | None:
    """Gráfico 2 de Sono: Estágios empilhados do sono (Profundo, REM, Leve, Acordado)."""
    stage_cols = [
        "deep_sleep_minutes",
        "rem_sleep_minutes",
        "light_sleep_minutes",
        "awake_minutes",
    ]
    stage_df = df.dropna(subset=stage_cols, how="all").copy()
    if stage_df.empty:
        return None

    dates = pd.to_datetime(stage_df["sleep_date"])
    fig = go.Figure()
    series_cfg = [
        ("deep_sleep_minutes", "Sono Profundo", "#1E3A8A"),
        ("rem_sleep_minutes", "Sono REM", "#7C3AED"),
        ("light_sleep_minutes", "Sono Leve", "#60A5FA"),
        ("awake_minutes", "Acordado", "#F59E0B"),
    ]
    for col, label, color in series_cfg:
        vals = stage_df[col].fillna(0.0)
        fig.add_trace(
            go.Bar(
                x=dates,
                y=vals,
                name=label,
                marker_color=color,
                hovertemplate="%{x|%d/%m/%Y}<br>" + label + ": %{y:.0f} min<extra></extra>",
            )
        )

    fig.update_layout(
        title="Arquitetura do Sono por Estágios (minutos)",
        barmode="stack",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        yaxis_title="Duração (min)",
        margin=dict(l=20, r=20, t=60, b=20),
        height=370,
    )
    return fig


def plot_autonomic_telemetry(df: pd.DataFrame) -> go.Figure | None:
    """Gráfico 3 de Sono: HRV (RMSSD) e FC de Repouso com faixa da baseline 28d."""
    has_hrv = df["hrv_rmssd"].notna().any()
    has_rhr = df["resting_hr"].notna().any()
    if not has_hrv and not has_rhr:
        return None

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=("HRV Noturna — RMSSD (ms)", "Frequência Cardíaca de Repouso (bpm)"),
    )

    if has_hrv:
        hrv_df = df.dropna(subset=["hrv_rmssd"]).copy()
        dates_hrv = pd.to_datetime(hrv_df["sleep_date"])
        upper_hrv = hrv_df["hrv_mean_28d"] + hrv_df["hrv_std_28d"]
        lower_hrv = (hrv_df["hrv_mean_28d"] - hrv_df["hrv_std_28d"]).clip(lower=0)

        fig.add_trace(
            go.Scatter(
                x=dates_hrv,
                y=upper_hrv,
                mode="lines",
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_hrv,
                y=lower_hrv,
                mode="lines",
                line=dict(width=0),
                fill="tonexty",
                fillcolor="rgba(16, 185, 129, 0.16)",
                name="Baseline HRV 28d (μ ± 1σ)",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_hrv,
                y=hrv_df["hrv_rmssd"],
                mode="lines+markers",
                name="HRV Diária (ms)",
                line=dict(color="#10B981", width=2.5),
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_hrv,
                y=hrv_df["hrv_ewma_7d"],
                mode="lines",
                name="HRV EWMA 7d",
                line=dict(color="#047857", width=2, dash="dot"),
            ),
            row=1,
            col=1,
        )

    if has_rhr:
        rhr_df = df.dropna(subset=["resting_hr"]).copy()
        dates_rhr = pd.to_datetime(rhr_df["sleep_date"])
        upper_rhr = rhr_df["rhr_mean_28d"] + rhr_df["rhr_std_28d"]
        lower_rhr = (rhr_df["rhr_mean_28d"] - rhr_df["rhr_std_28d"]).clip(lower=25)

        fig.add_trace(
            go.Scatter(
                x=dates_rhr,
                y=upper_rhr,
                mode="lines",
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_rhr,
                y=lower_rhr,
                mode="lines",
                line=dict(width=0),
                fill="tonexty",
                fillcolor="rgba(239, 68, 68, 0.14)",
                name="Baseline FC Repouso 28d (μ ± 1σ)",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_rhr,
                y=rhr_df["resting_hr"],
                mode="lines+markers",
                name="FC Repouso (bpm)",
                line=dict(color="#EF4444", width=2.5),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates_rhr,
                y=rhr_df["rhr_ewma_7d"],
                mode="lines",
                name="FC Repouso EWMA 7d",
                line=dict(color="#991B1B", width=2, dash="dot"),
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        title="Telemetria Autonômica Noturna (HRV & FC de Repouso)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        margin=dict(l=20, r=20, t=80, b=20),
        height=520,
    )
    fig.update_yaxes(title_text="ms", row=1, col=1)
    fig.update_yaxes(title_text="bpm", row=2, col=1)
    return fig


# --- Sidebar ---
with st.sidebar:
    st.title("🏃 Monitor Fit")
    st.caption("Passos Diários Reais + Sono & Recuperação")

    target_steps = st.slider(
        "Meta diária de passos",
        min_value=4000,
        max_value=25000,
        value=int(settings.STEPS_TARGET),
        step=500,
    )
    target_hours = st.slider(
        "Meta diária de sono (h)",
        min_value=6.0,
        max_value=10.0,
        value=float(settings.SLEEP_TARGET_HOURS),
        step=0.5,
    )

    steps_df, steps_summary, sleep_df, readiness, relation = load_all_analytics(
        target_steps=target_steps,
        target_hours=target_hours,
    )

    c_sb1, c_sb2 = st.columns(2)
    c_sb1.metric("Dias c/ Passos", len(steps_df))
    c_sb2.metric("Noites de Sono", len(sleep_df))

    db_kind = "SQLite Local" if settings.DATABASE_URL.startswith("sqlite") else "PostgreSQL / Cloud"
    st.caption(f"Banco ativo: **{db_kind}**")
    st.divider()
    st.markdown(
        "**Evolução do Projeto**\n"
        "- ✅ **Passos Diários**: Webhook rápido Android, CSV Samsung Health & ACWR\n"
        "- ✅ **Sono & Cruzamento**: Pronto para receber dados reais de sono\n"
        "- 🔜 **Strava + Clima**: Telemetria de corrida & Open-Meteo Campeche"
    )

st.title("Monitor de Desempenho & Fadiga — Passos Reais + Sono")

tab_steps, tab_relation, tab_sleep, tab_import, tab_mobile = st.tabs(
    [
        "👟 Passos Diários & Carga (ACWR)",
        "🔗 Relação Passos × Sono",
        "😴 Sono & Prontidão (Detalhado)",
        "📂 Importar Samsung Health (Passos & Sono)",
        "📲 Conectar Celular Android (Passos Reais)",
    ]
)

today_local = datetime.now(ZoneInfo(settings.TIMEZONE)).date()

# --- Aba 1: Passos Diários & Carga (ACWR) ---
with tab_steps:
    with st.expander("⚡ Lançar Passos Reais de Hoje (ou de outra data)", expanded=steps_df.empty):
        with st.form("quick_steps_form"):
            q1, q2, q3, q4 = st.columns(4)
            q_date = q1.date_input("Data", value=today_local, key="q_step_date")
            q_steps = q2.number_input("Passos do dia", min_value=1, max_value=250000, value=8500, step=100)
            q_dist = q3.number_input(
                "Distância (km) — opcional",
                min_value=0.0,
                max_value=300.0,
                value=None,
                step=0.1,
                help="Se vazio, será estimada pelos passos.",
            )
            q_active = q4.number_input(
                "Minutos ativos — opcional",
                min_value=0.0,
                max_value=1440.0,
                value=None,
                step=5.0,
            )
            q_notes = st.text_input("Observação (opcional)", placeholder="Ex: Passos reais do Samsung Health hoje")

            if st.form_submit_button("💾 Salvar Passos", type="primary"):
                payload = StepsIn(
                    step_date=q_date,
                    steps_count=int(q_steps),
                    goal_steps=target_steps,
                    distance_km=q_dist,
                    active_minutes=q_active,
                    source="manual",
                    notes=q_notes or None,
                )
                with SessionLocal() as db:
                    saved = upsert_steps_log(db, payload, raw_json=payload.model_dump_json())
                st.success(f"Salvo: {saved.steps_count:,} passos em {saved.step_date}!".replace(",", "."))
                st.rerun()

    if steps_df.empty:
        st.info(
            "Nenhum registro de passos encontrado ainda. "
            "Você pode lançar os passos reais de hoje acima, conectar seu celular Android na última aba, "
            "importar o CSV do Samsung Health ou carregar 30 dias de exemplo para visualizar os gráficos."
        )
        if st.button("✨ Carregar 30 Dias de Exemplo (Passos + Sono)", type="primary"):
            n_steps, n_sleep = save_sample_datasets(days=30, include_sleep=True)
            st.success(f"Carregados {n_steps} dias de passos e {n_sleep} noites de sono!")
            st.rerun()
    else:
        st_color = steps_summary["status_color"]
        banner_msg = f"**{steps_summary['status_label']}** — Último registro: `{steps_summary['latest_date']}`"
        if st_color == "green":
            st.success(banner_msg)
        elif st_color == "orange":
            st.warning(banner_msg)
        else:
            st.info(banner_msg)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Passos Último Dia", f"{steps_summary['last_steps']:,}".replace(",", "."))
        m2.metric("% da Meta", f"{steps_summary['goal_pct']:.1f}%")
        m3.metric("Distância", f"{steps_summary['last_distance_km']:.2f} km")
        m4.metric("Acumulado 7d", f"{steps_summary['rolling_steps_7d']:,}".replace(",", "."))
        m5.metric("ACWR Passos", f"{steps_summary['steps_acwr']:.2f}")

        with st.expander("💡 Insights de Volume Ambulatorial", expanded=True):
            for insight in steps_summary["insights"]:
                st.markdown(f"- {insight}")

        st.plotly_chart(plot_steps_and_ewma(steps_df, target_steps), use_container_width=True)
        st.plotly_chart(plot_steps_acwr(steps_df), use_container_width=True)

        st.divider()
        st.subheader("Histórico de Passos")
        steps_table = steps_df.sort_values("step_date", ascending=False)[
            [
                "step_date",
                "steps_count",
                "goal_pct",
                "distance_km",
                "active_minutes",
                "calories_kcal",
                "steps_acwr",
                "source",
            ]
        ]
        st.dataframe(steps_table, use_container_width=True, hide_index=True)

        d_col1, d_col2 = st.columns([2, 1])
        step_dates = [str(d) for d in steps_table["step_date"].tolist()]
        del_step_date = d_col1.selectbox("Selecionar data de passos para excluir", step_dates)
        if d_col2.button("🗑️ Excluir Passos da Data"):
            with SessionLocal() as db:
                removed = delete_steps_log_by_date(db, date.fromisoformat(del_step_date))
            if removed:
                st.success(f"Passos de {del_step_date} removidos.")
                st.rerun()

# --- Aba 2: Relação Passos × Sono ---
with tab_relation:
    st.subheader("Relação Passos Diários × Sono e Recuperação")
    rel_df = relation["df"]

    for item in relation["insights"]:
        st.markdown(f"- {item}")

    c_r1, c_r2, c_r3, c_r4 = st.columns(4)
    c_r1.metric(
        "Sono Manhã → Passos",
        f"r = {relation['corr_sleep_to_steps']:+.2f}" if relation["corr_sleep_to_steps"] is not None else "N/D",
    )
    c_r2.metric(
        "Passos → Sono Profundo Seg.",
        f"r = {relation['corr_steps_to_next_deep']:+.2f}"
        if relation["corr_steps_to_next_deep"] is not None
        else "N/D",
    )
    c_r3.metric(
        "Passos → HRV Seguinte",
        f"r = {relation['corr_steps_to_next_hrv']:+.2f}"
        if relation["corr_steps_to_next_hrv"] is not None
        else "N/D",
    )
    c_r4.metric(
        "Passos → FC Repouso Seg.",
        f"r = {relation['corr_steps_to_next_rhr']:+.2f}"
        if relation["corr_steps_to_next_rhr"] is not None
        else "N/D",
    )

    if relation["has_steps"] and relation["has_sleep"]:
        st.plotly_chart(plot_steps_vs_sleep_timeline(rel_df), use_container_width=True)

        y_choice = st.selectbox(
            "Métrica da noite seguinte para gráfico de dispersão",
            options=["next_sleep_hours", "next_deep_minutes", "next_hrv"],
            format_func=lambda k: {
                "next_sleep_hours": "Horas de Sono (Noite Seguinte)",
                "next_deep_minutes": "Sono Profundo (min — Noite Seguinte)",
                "next_hrv": "HRV RMSSD (ms — Noite Seguinte)",
            }[k],
        )
        scatter_fig = plot_steps_sleep_scatter(rel_df, y_choice)
        if scatter_fig is not None:
            st.plotly_chart(scatter_fig, use_container_width=True)
        else:
            st.info("Ainda não há pares suficientes (Passos + métrica de sono da noite seguinte) para dispersão.")
    else:
        st.info(
            "Assim que houver registros de **Passos** e de **Sono** para as mesmas datas, "
            "os gráficos de série combinada e dispersão aparecerão aqui."
        )
        if st.button("✨ Carregar 30 Dias de Exemplo (Passos + Sono) para Visualizar Cruzamento"):
            n_steps, n_sleep = save_sample_datasets(days=30, include_sleep=True)
            st.success(f"Carregados {n_steps} dias de passos e {n_sleep} noites de sono!")
            st.rerun()

# --- Aba 3: Sono & Prontidão (Detalhado) ---
with tab_sleep:
    if sleep_df.empty:
        st.info(
            "Nenhum registro de sono no banco ainda. "
            "Quando quiser começar a integrar dados reais de sono, basta enviar pelo Webhook `/api/v1/sleep`, "
            "importar o CSV do Samsung Health ou registrar abaixo."
        )
    else:
        status_color = readiness["status_color"]
        banner_text = f"**{readiness['status_label']}** — Última noite registrada: `{readiness['latest_date']}`"
        if status_color == "green":
            st.success(banner_text)
        elif status_color == "orange":
            st.warning(banner_text)
        else:
            st.error(banner_text)

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Readiness Score", f"{readiness['readiness_score']:.0f}/100")
        k2.metric("Última Noite", f"{readiness['last_duration_hours']:.2f} h")
        k3.metric("Dívida Acumulada 7d", f"{readiness['sleep_debt_7d_hours']:.2f} h")
        k4.metric("Score de Sono", f"{readiness['last_sleep_score']:.0f}/100")
        k5.metric("Consistência (7d/28d)", f"{readiness['sleep_consistency_ratio']:.2f}")

        c_status1, c_status2 = st.columns(2)
        c_status1.caption(f"**HRV:** {readiness['hrv_status']}")
        c_status2.caption(f"**FC de Repouso:** {readiness['rhr_status']}")

        with st.expander("💡 Insights Fisiológicos do Sono", expanded=True):
            for insight in readiness["insights"]:
                st.markdown(f"- {insight}")

        st.plotly_chart(plot_duration_and_ewma(sleep_df, target_hours), use_container_width=True)

        arch_fig = plot_sleep_architecture(sleep_df)
        if arch_fig is not None:
            st.plotly_chart(arch_fig, use_container_width=True)

        auto_fig = plot_autonomic_telemetry(sleep_df)
        if auto_fig is not None:
            st.plotly_chart(auto_fig, use_container_width=True)

    st.divider()
    st.subheader("Registrar ou Atualizar Noite de Sono")
    with st.form("manual_sleep_form"):
        col1, col2, col3 = st.columns(3)
        sleep_date_val = col1.date_input("Data ao acordar", value=today_local)
        bed_time_val = col2.time_input("Hora que deitou", value=time(23, 0))
        wake_time_val = col3.time_input("Hora que acordou", value=time(6, 45))

        col4, col5, col6, col7 = st.columns(4)
        duration_hours_override = col4.number_input(
            "Duração total (h) — opcional",
            min_value=0.0,
            max_value=24.0,
            value=None,
            step=0.25,
        )
        deep_min = col5.number_input("Sono Profundo (min)", min_value=0.0, max_value=720.0, value=None, step=5.0)
        rem_min = col6.number_input("Sono REM (min)", min_value=0.0, max_value=720.0, value=None, step=5.0)
        light_min = col7.number_input("Sono Leve (min)", min_value=0.0, max_value=900.0, value=None, step=5.0)

        col8, col9, col10, col11 = st.columns(4)
        awake_min = col8.number_input("Acordado na noite (min)", min_value=0.0, max_value=360.0, value=None, step=5.0)
        resting_hr_val = col9.number_input("FC Repouso (bpm)", min_value=25.0, max_value=200.0, value=None, step=1.0)
        hrv_val = col10.number_input("HRV RMSSD (ms)", min_value=1.0, max_value=300.0, value=None, step=1.0)
        spo2_val = col11.number_input("SpO2 médio (%)", min_value=70.0, max_value=100.0, value=None, step=0.5)

        col12, col13 = st.columns(2)
        sleep_score_val = col12.number_input(
            "Score Samsung Health (0-100) — opcional",
            min_value=0.0,
            max_value=100.0,
            value=None,
            step=1.0,
        )
        feeling_val = col13.slider("Percepção ao acordar (1 = exausto, 5 = ótimo)", 1, 5, 4)
        notes_val = st.text_input("Observações", placeholder="Ex: Acordei bem antes do treino longo")

        if st.form_submit_button("💾 Salvar Registro de Sono", type="primary"):
            wake_dt = datetime.combine(sleep_date_val, wake_time_val)
            bed_date = sleep_date_val - timedelta(days=1) if bed_time_val > wake_time_val else sleep_date_val
            bed_dt = datetime.combine(bed_date, bed_time_val)
            try:
                payload = SleepIn(
                    sleep_date=sleep_date_val,
                    bedtime=bed_dt,
                    wake_time=wake_dt,
                    duration_hours=duration_hours_override if duration_hours_override and duration_hours_override > 0 else None,
                    deep_sleep_minutes=deep_min,
                    rem_sleep_minutes=rem_min,
                    light_sleep_minutes=light_min,
                    awake_minutes=awake_min,
                    sleep_score=sleep_score_val,
                    resting_hr=resting_hr_val,
                    hrv_rmssd=hrv_val,
                    spo2_avg=spo2_val,
                    subjective_feeling=feeling_val,
                    source="manual",
                    notes=notes_val or None,
                )
                with SessionLocal() as db:
                    saved = upsert_sleep_log(db, payload, raw_json=payload.model_dump_json())
                st.success(f"Registro de {saved.sleep_date} salvo!")
                st.rerun()
            except ValueError as exc:
                st.error(f"Erro ao validar os dados: {exc}")

    if not sleep_df.empty:
        st.subheader("Histórico de Sono")
        sleep_table = sleep_df.sort_values("sleep_date", ascending=False)[
            [
                "sleep_date",
                "duration_hours",
                "sleep_score",
                "sleep_efficiency",
                "deep_sleep_minutes",
                "rem_sleep_minutes",
                "resting_hr",
                "hrv_rmssd",
                "source",
            ]
        ]
        st.dataframe(sleep_table, use_container_width=True, hide_index=True)

        ds_col1, ds_col2 = st.columns([2, 1])
        sleep_dates = [str(d) for d in sleep_table["sleep_date"].tolist()]
        del_sleep_date = ds_col1.selectbox("Selecionar data de sono para excluir", sleep_dates)
        if ds_col2.button("🗑️ Excluir Sono da Data"):
            with SessionLocal() as db:
                removed = delete_sleep_log_by_date(db, date.fromisoformat(del_sleep_date))
            if removed:
                st.success(f"Sono de {del_sleep_date} removido.")
                st.rerun()

# --- Aba 4: Importar Samsung Health (Passos & Sono) ---
with tab_import:
    st.subheader("Importar Exportação do Samsung Health, CSV ou JSON")

    import_kind = st.radio(
        "Tipo de dados do arquivo",
        options=["steps", "sleep"],
        format_func=lambda v: (
            "👟 Passos Diários (step_daily_trend / pedometer_day_summary / CSV / JSON)"
            if v == "steps"
            else "😴 Sono (shealth.sleep / CSV / JSON)"
        ),
        horizontal=True,
    )

    c_tmpl1, c_tmpl2, c_sample = st.columns(3)
    c_tmpl1.download_button(
        label="⬇️ Template CSV (Passos)",
        data=generate_steps_csv_template(),
        file_name="template_passos.csv",
        mime="text/csv",
    )
    c_tmpl2.download_button(
        label="⬇️ Template CSV (Sono)",
        data=generate_csv_template(),
        file_name="template_sono.csv",
        mime="text/csv",
    )
    if c_sample.button("✨ Gerar 30 Dias de Exemplo (Passos + Sono)"):
        n_steps, n_sleep = save_sample_datasets(days=30, include_sleep=True)
        st.success(f"Salvos {n_steps} dias de passos e {n_sleep} noites de sono!")
        st.rerun()

    uploaded = st.file_uploader("Enviar arquivo (.csv ou .json)", type=["csv", "json"])
    if uploaded is not None:
        raw_bytes = uploaded.read()
        try:
            is_json = uploaded.name.lower().endswith(".json")
            if import_kind == "steps":
                parsed_steps = parse_steps_json(raw_bytes) if is_json else parse_samsung_health_or_generic_steps_csv(raw_bytes)
                if not parsed_steps:
                    st.warning("Nenhum registro válido de passos encontrado no arquivo.")
                else:
                    st.info(f"**{len(parsed_steps)}** registros diários de passos encontrados.")
                    preview = [
                        {
                            "step_date": r.step_date,
                            "steps_count": r.steps_count,
                            "distance_km": r.distance_km,
                            "active_minutes": r.active_minutes,
                            "calories_kcal": r.calories_kcal,
                            "source": r.source,
                        }
                        for r in parsed_steps[:15]
                    ]
                    st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
                    if st.button("📥 Importar Passos para o Banco", type="primary"):
                        with SessionLocal() as db:
                            saved = upsert_steps_batch(db, parsed_steps)
                        st.success(f"{len(saved)} registros de passos importados com sucesso!")
                        st.rerun()
            else:
                parsed_sleep = parse_sleep_json(raw_bytes) if is_json else parse_samsung_health_or_generic_csv(raw_bytes)
                if not parsed_sleep:
                    st.warning("Nenhum registro válido de sono encontrado no arquivo.")
                else:
                    st.info(f"**{len(parsed_sleep)}** registros de sono encontrados.")
                    preview = [
                        {
                            "sleep_date": r.sleep_date,
                            "duration_hours": round((r.duration_minutes or 0) / 60.0, 2),
                            "deep_min": r.deep_sleep_minutes,
                            "rem_min": r.rem_sleep_minutes,
                            "score": r.sleep_score,
                            "source": r.source,
                        }
                        for r in parsed_sleep[:15]
                    ]
                    st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
                    if st.button("📥 Importar Sono para o Banco", type="primary"):
                        with SessionLocal() as db:
                            saved = upsert_sleep_batch(db, parsed_sleep)
                        st.success(f"{len(saved)} registros de sono importados com sucesso!")
                        st.rerun()
        except Exception as exc:
            st.error(f"Falha ao processar arquivo: {exc}")

# --- Aba 5: Conectar Celular Android (Passos Reais) ---
with tab_mobile:
    st.subheader("Conectar Celular Android — Passos Reais Agora")
    lan_ip = get_local_lan_ip()
    dashboard_mobile_url = f"http://{lan_ip}:8501"
    steps_api_url = f"http://{lan_ip}:8000/api/v1/steps"
    steps_quick_url = f"http://{lan_ip}:8000/api/v1/steps/quick?steps=8500"

    st.markdown(
        f"""
        ### 3 formas rápidas de enviar seus **passos reais** do Android:

        #### 1. Pelo navegador do celular (mais rápido, sem instalar nada)
        Com o celular na mesma rede Wi-Fi do PC, abra no Chrome do Android:
        ```text
        {dashboard_mobile_url}
        ```
        E preencha o campo **⚡ Lançar Passos Reais de Hoje** na primeira aba.

        #### 2. Automático em 1 clique / rotina diária (MacroDroid, Tasker ou HTTP Shortcuts)
        - **URL direta (sem precisar montar JSON):**
          `POST {steps_quick_url}`
        - **Ou enviando JSON para:**
          `POST {steps_api_url}`
        """
    )

    sample_steps_payload = {
        "step_date": str(today_local),
        "steps": 9450,
        "source": "samsung_health_android",
        "notes": "Passos reais enviados do celular",
    }
    st.code(json.dumps(sample_steps_payload, indent=2, ensure_ascii=False), language="json")

    st.markdown(
        """
        #### 3. Importar todo o histórico real do Samsung Health
        No celular: abra **Samsung Health → Configurações → Baixar dados pessoais**.
        Depois envie o arquivo `com.samsung.shealth.step_daily_trend.*.csv` (ou `pedometer_day_summary`) na aba **📂 Importar Samsung Health**.
        """
    )

    if st.button("🧪 Simular Webhook de Passos (Hoje)", type="primary"):
        validated = StepsIn.model_validate(sample_steps_payload)
        with SessionLocal() as db:
            saved = upsert_steps_log(db, validated, raw_json=validated.model_dump_json())
        out = StepsOut.model_validate(saved)
        st.success(f"Passos simulados salvos para {out.step_date}: {out.steps_count:,} passos!".replace(",", "."))
        st.json(out.model_dump(mode="json"))

    with st.expander("😴 Endpoint de Sono (para as próximas versões)"):
        st.code(f"POST http://{lan_ip}:8000/api/v1/sleep", language="text")
        st.caption(
            "Quando formos integrar os dados reais de sono, basta enviar o JSON de sono para esse endpoint "
            "ou importar `com.samsung.shealth.sleep.*.csv`."
        )
