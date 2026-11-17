# Monitor de Saúde e Desempenho (v1)

Este projeto é uma pipeline local e modular em Python para coleta, modelagem temporal (EWMA 7d/28d, ACWR de passos, dívida de sono, baselines de HRV e FC de repouso) e visualização integrada dos seus **Passos Diários Reais** e **Sono / Recuperação** via **Samsung Health / Health Connect (Android)**. A ideia é poder visualizar melhor os dados que o app nativo não fornece, como pontuação de sono ao longo do tempo, média global e carga de passos, e correlações entre atividade diária e qualidade do sono.

Projetado para rodar localmente com **SQLite** e migrar sem alterações de código para **PostgreSQL (Supabase / Neon)** apenas configurando `DATABASE_URL` no `.env`.

## Estrutura do Projeto

- `app/config.py`: Carregamento de variáveis de ambiente (`.env`) e metas padrão (`SLEEP_TARGET_HOURS`, `STEPS_TARGET`).
- `app/database.py`: Conexão SQLAlchemy 2.0 (SQLite local ou PostgreSQL via `DATABASE_URL`).
- `app/models.py`: Tabelas `daily_steps` (passos diários, distância, tempo ativo, calorias, andares) e `sleep_logs` (duração, estágios Profundo/REM/Leve/Acordado, eficiência, score, FC repouso, HRV RMSSD, SpO2, taxa respiratória, variação térmica da pele).
- `app/schemas.py`: Validação Pydantic flexível para passos (`StepsIn`, `StepsOut`, `StepsBatchIn`) e sono (`SleepIn`, `SleepOut`, `SleepBatchIn`, `SleepStageItem`).
- `app/services/steps_service.py`: Upsert diário de passos por `step_date`, importação em lote e consultas.
- `app/services/sleep_service.py`: Upsert diário de sono por `sleep_date` e cálculo automático de métricas derivadas.
- `app/services/analytics.py`: Séries temporais com **EWMA 7d vs. 28d**, **ACWR de Passos**, dívida de sono acumulada 7d, baselines autonômicas ($\mu \pm 1\sigma$), índice de **Prontidão Diária** e **Cruzamento Passos × Sono**.
- `app/services/importers.py`: Parsers para CSVs oficiais do Samsung Health (`com.samsung.shealth.step_daily_trend.*.csv`, `pedometer_day_summary`, `com.samsung.shealth.sleep.*.csv`), CSV genérico, JSON e geradores de 30 dias de exemplo.
- `app/api.py`: API HTTP / Webhook em **FastAPI** (`/api/v1/steps`, `/api/v1/steps/quick`, `/api/v1/analytics/steps-sleep`, `/api/v1/sleep` e `/api/v1/sleep/readiness`).
- `dashboard.py`: Painel interativo em **Streamlit** (5 abas com gráficos **Plotly**, entrada rápida de passos/sono, importador Samsung Health e guia de conexão Android).
- `run.py`: Lançador unificado que sobe a API FastAPI e o Dashboard Streamlit juntos.
- `tests/test_sleep_pipeline.py`: Testes automatizados com `pytest`.

## Como Executar Localmente

### 1. Instalar dependências

```powershell
pip install -r requirements.txt
```

*(Opcional)* Copie `.env.example` para `.env` caso queira definir `WEBHOOK_API_KEY`, `STEPS_TARGET`, `SLEEP_TARGET_HOURS` ou apontar `DATABASE_URL` para um PostgreSQL/Supabase.

### 2. Iniciar API + Dashboard com um comando (recomendado)

```powershell
python run.py
```

Depois acesse no navegador:
- **Dashboard (Streamlit):** `http://localhost:8501`
- **Documentação da API (Swagger):** `http://localhost:8000/docs`

### 3. Ou iniciar os serviços separadamente

Webhook HTTP (FastAPI):
```powershell
python -m uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

Dashboard (Streamlit):
```powershell
python -m streamlit run dashboard.py
```

### 4. Rodar os Testes Automatizados

```powershell
python -m pytest -v
```

## Endpoints Principais (Celular Android / Webhook)

- `POST /api/v1/steps` — Recebe JSON de passos (ex: `{"steps": 9450}`) e atualiza a data correspondente.
- `POST /api/v1/steps/quick?steps=8500` — Envio rápido via query string para automações Android (MacroDroid / Tasker / HTTP Shortcuts).
- `GET /api/v1/steps/summary` — Resumo com EWMA 7d/28d e ACWR de passos.
- `GET /api/v1/analytics/steps-sleep` — Cruzamento estatístico entre Passos Diários e Sono.
- `POST /api/v1/sleep` — Recebe dados de sono e calcula eficiência/score derivados.
- `GET /api/v1/sleep/readiness` — Índice diário de prontidão e dívida de sono 7d.

## Roadmap Evolutivo (Próximas Versões)

- **v1.5 — Dados Reais de Sono**:
  - Conexão contínua dos registros reais de sono (HRV, FC de repouso e estágios) do Samsung Health / Health Connect para enriquecer o cruzamento Passos × Sono.
- **v2 — Telemetria de Corrida & Clima**:
  - Coleta automatizada da API do **Strava** (distância, pace, GAP, FC, cadência, streams) via Python / GitHub Actions.
  - Cruzamento com meteorologia horária real da API **Open-Meteo** usando as coordenadas da corrida ou fallback para **Florianópolis**, incluindo Ponto de Orvalho (*Dew Point*), Temperatura Aparente e Vento.
  - Modelagem combinada de **ACWR (TRIMP & Volume via EWMA 7d/28d)**, **Impacto Térmico no Pace**, **Efficiency Factor** e **Prontidão Integrada (Sono + Passos + Carga de Treino)**.
