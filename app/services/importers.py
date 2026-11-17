"""Importadores (Samsung Health CSV, CSV genérico, JSON) e gerador de dados de exemplo."""

import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import json
import random
from zoneinfo import ZoneInfo
import pandas as pd

from app.config import settings
from app.schemas import SleepIn, StepsIn

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sleep_date": ("sleep_date", "date", "data", "day", "dia"),
    "bedtime": ("bedtime", "start_time", "sleep_start", "hora_dormir", "inicio"),
    "wake_time": ("wake_time", "end_time", "sleep_end", "hora_acordar", "fim"),
    "duration_minutes": (
        "duration_minutes",
        "sleep_duration",
        "total_sleep_duration",
        "duration",
        "minutos_dormidos",
    ),
    "duration_hours": ("duration_hours", "hours", "horas", "horas_dormidas"),
    "time_in_bed_minutes": ("time_in_bed_minutes", "time_in_bed", "tempo_na_cama"),
    "deep_sleep_minutes": (
        "deep_sleep_minutes",
        "total_deep_duration",
        "deep_duration",
        "deep",
        "sono_profundo",
        "profundo",
    ),
    "light_sleep_minutes": (
        "light_sleep_minutes",
        "total_light_duration",
        "light_duration",
        "light",
        "sono_leve",
        "leve",
    ),
    "rem_sleep_minutes": (
        "rem_sleep_minutes",
        "total_rem_duration",
        "rem_duration",
        "rem",
        "sono_rem",
    ),
    "awake_minutes": (
        "awake_minutes",
        "total_awake_duration",
        "awake_duration",
        "awake",
        "acordado",
    ),
    "sleep_efficiency": ("sleep_efficiency", "efficiency", "eficiencia"),
    "sleep_score": ("sleep_score", "score", "pontuacao"),
    "resting_hr": ("resting_hr", "rhr", "heart_rate_resting", "fc_repouso"),
    "hrv_rmssd": ("hrv_rmssd", "hrv", "rmssd"),
    "spo2_avg": ("spo2_avg", "spo2", "blood_oxygen"),
    "respiratory_rate": ("respiratory_rate", "resp_rate", "frequencia_respiratoria"),
    "skin_temp_delta": ("skin_temp_delta", "skin_temperature_delta", "temp_pele"),
    "subjective_feeling": ("subjective_feeling", "feeling", "percepcao"),
    "notes": ("notes", "observacoes", "comentarios"),
}


def _decode_content(file_content: bytes | str) -> str:
    if isinstance(file_content, str):
        return file_content
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_content.decode("utf-8", errors="replace")


def _normalize_duration_minutes(value: float | None) -> float | None:
    """Converte automaticamente durações vindas em milissegundos ou segundos para minutos."""
    if value is None or pd.isna(value):
        return None
    num = float(value)
    if num <= 0:
        return None
    if num > 86400:
        return round(num / 60000.0, 2)
    if num > 1440:
        return round(num / 60.0, 2)
    return round(num, 2)


def _normalize_column_name(col: str) -> str:
    cleaned = str(col).strip().lower().replace(" ", "_")
    for prefix in ("com.samsung.health.sleep.", "com.samsung.shealth.sleep."):
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix)
    return cleaned


def _detect_separator(header_line: str) -> str:
    if ";" in header_line and header_line.count(";") >= header_line.count(","):
        return ";"
    return ","


def _parse_datetime_safe(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()


def _parse_date_safe(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date()


def _parse_float_safe(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", ".") if isinstance(value, str) else value
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_samsung_health_or_generic_csv(file_content: bytes | str) -> list[SleepIn]:
    """Lê CSV exportado do Samsung Health ou CSV genérico e converte em SleepIn."""
    text = _decode_content(file_content).strip()
    if not text:
        return []

    lines = text.splitlines()
    first_line_lower = lines[0].strip().lower()
    is_samsung_export = first_line_lower.startswith("com.samsung.")
    skip_rows = 1 if is_samsung_export else 0

    header_idx = 1 if is_samsung_export and len(lines) > 1 else 0
    sep = _detect_separator(lines[header_idx])

    df = pd.read_csv(
        io.StringIO(text),
        skiprows=skip_rows,
        sep=sep,
        index_col=False,
    )
    if df.empty:
        return []

    normalized_cols = {_normalize_column_name(col): col for col in df.columns}
    col_map: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_cols:
                col_map[canonical] = normalized_cols[alias]
                break

    source_default = "samsung_health" if is_samsung_export else "csv_import"
    parsed_by_date: dict[date, SleepIn] = {}

    for _, row in df.iterrows():
        raw: dict[str, object] = {"source": source_default}

        if "sleep_date" in col_map:
            parsed_date = _parse_date_safe(row[col_map["sleep_date"]])
            if parsed_date is not None:
                raw["sleep_date"] = parsed_date

        if "bedtime" in col_map:
            bedtime = _parse_datetime_safe(row[col_map["bedtime"]])
            if bedtime is not None:
                raw["bedtime"] = bedtime

        if "wake_time" in col_map:
            wake_time = _parse_datetime_safe(row[col_map["wake_time"]])
            if wake_time is not None:
                raw["wake_time"] = wake_time

        for dur_col in (
            "duration_minutes",
            "time_in_bed_minutes",
            "deep_sleep_minutes",
            "light_sleep_minutes",
            "rem_sleep_minutes",
            "awake_minutes",
        ):
            if dur_col in col_map:
                norm_val = _normalize_duration_minutes(_parse_float_safe(row[col_map[dur_col]]))
                if norm_val is not None:
                    raw[dur_col] = norm_val

        if "duration_hours" in col_map and "duration_minutes" not in raw:
            hours_val = _parse_float_safe(row[col_map["duration_hours"]])
            if hours_val is not None and hours_val > 0:
                raw["duration_hours"] = hours_val

        for float_col in (
            "sleep_efficiency",
            "sleep_score",
            "resting_hr",
            "hrv_rmssd",
            "spo2_avg",
            "respiratory_rate",
            "skin_temp_delta",
        ):
            if float_col in col_map:
                val = _parse_float_safe(row[col_map[float_col]])
                if val is not None:
                    raw[float_col] = val

        if "subjective_feeling" in col_map:
            feeling_val = _parse_float_safe(row[col_map["subjective_feeling"]])
            if feeling_val is not None:
                raw["subjective_feeling"] = int(round(feeling_val))

        if "notes" in col_map:
            note_val = row[col_map["notes"]]
            if pd.notna(note_val) and str(note_val).strip():
                raw["notes"] = str(note_val).strip()

        try:
            item = SleepIn.model_validate(raw)
        except ValueError:
            continue

        # Se houver múltiplas sessões no mesmo dia, mantém a principal (maior duração)
        if item.sleep_date is not None:
            existing = parsed_by_date.get(item.sleep_date)
            if existing is None or (item.duration_minutes or 0) >= (existing.duration_minutes or 0):
                parsed_by_date[item.sleep_date] = item

    return sorted(parsed_by_date.values(), key=lambda x: x.sleep_date or date.min)


def parse_sleep_json(file_content: bytes | str) -> list[SleepIn]:
    """Lê arquivo JSON (objeto único, lista ou {'records': [...]}) em SleepIn."""
    text = _decode_content(file_content).strip()
    if not text:
        return []
    payload = json.loads(text)
    if isinstance(payload, dict):
        items = payload["records"] if "records" in payload else [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("Formato JSON inválido: esperado objeto ou lista de registros.")
    return [SleepIn.model_validate(item) for item in items]


def generate_csv_template() -> str:
    """Retorna um CSV modelo pronto para preenchimento ou importação."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "sleep_date",
            "bedtime",
            "wake_time",
            "duration_minutes",
            "deep_sleep_minutes",
            "light_sleep_minutes",
            "rem_sleep_minutes",
            "awake_minutes",
            "sleep_score",
            "resting_hr",
            "hrv_rmssd",
            "spo2_avg",
            "notes",
        ]
    )
    writer.writerow(
        [
            "2026-11-16",
            "2026-11-15T22:50:00",
            "2026-11-16T06:35:00",
            "445",
            "92",
            "248",
            "105",
            "20",
            "86",
            "49",
            "71",
            "97",
            "Noite tranquila pós-rodagem",
        ]
    )
    return buffer.getvalue()


def generate_sample_sleep_data(days: int = 30) -> list[SleepIn]:
    """Gera uma série sintética realista de sono para um corredor usando Samsung Health."""
    rng = random.Random(42)
    today = datetime.now(ZoneInfo(settings.TIMEZONE)).date()
    records: list[SleepIn] = []

    for offset in range(days - 1, -1, -1):
        wake_day = today - timedelta(days=offset)
        is_weekend = wake_day.weekday() in (5, 6)

        # Simula 2 dias recentes com sono mais curto para evidenciar variações de prontidão
        if offset in (2, 1):
            total_sleep_min = rng.uniform(345, 385)
            rhr = rng.uniform(53, 57)
            hrv = rng.uniform(50, 58)
            feeling = 2
        elif is_weekend:
            total_sleep_min = rng.uniform(470, 525)
            rhr = rng.uniform(46, 51)
            hrv = rng.uniform(70, 85)
            feeling = 5
        else:
            total_sleep_min = rng.uniform(425, 485)
            rhr = rng.uniform(47, 53)
            hrv = rng.uniform(62, 79)
            feeling = rng.choice([3, 4, 4, 5])

        deep_pct = rng.uniform(0.17, 0.23)
        rem_pct = rng.uniform(0.19, 0.25)
        deep_min = round(total_sleep_min * deep_pct, 1)
        rem_min = round(total_sleep_min * rem_pct, 1)
        light_min = round(total_sleep_min - deep_min - rem_min, 1)
        awake_min = round(rng.uniform(10, 32), 1)

        wake_hour = 7 if is_weekend else 6
        wake_minute = rng.randint(15, 50)
        wake_dt = datetime.combine(wake_day, time(wake_hour, wake_minute))
        bed_dt = wake_dt - timedelta(minutes=total_sleep_min + awake_min)

        records.append(
            SleepIn(
                sleep_date=wake_day,
                bedtime=bed_dt,
                wake_time=wake_dt,
                duration_minutes=round(total_sleep_min, 1),
                time_in_bed_minutes=round(total_sleep_min + awake_min, 1),
                deep_sleep_minutes=deep_min,
                light_sleep_minutes=light_min,
                rem_sleep_minutes=rem_min,
                awake_minutes=awake_min,
                resting_hr=round(rhr, 1),
                hrv_rmssd=round(hrv, 1),
                spo2_avg=round(rng.uniform(95.4, 98.4), 1),
                respiratory_rate=round(rng.uniform(13.2, 15.8), 1),
                skin_temp_delta=round(rng.uniform(-0.3, 0.4), 2),
                subjective_feeling=feeling,
                source="samsung_health",
                notes="Dados simulados Samsung Health / Health Connect",
            )
        )

    return records


STEPS_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "step_date": ("step_date", "day_time", "date", "data", "day", "dia", "create_time", "start_time"),
    "steps_count": ("steps_count", "steps", "step_count", "count", "passos", "total_steps"),
    "goal_steps": ("goal_steps", "goal", "meta", "target"),
    "distance_km": ("distance_km", "distance", "distancia"),
    "active_minutes": ("active_minutes", "active_time", "minutos_ativos"),
    "calories_kcal": ("calories_kcal", "calorie", "calories", "calorias"),
    "floors_climbed": ("floors_climbed", "floors", "andares"),
    "notes": ("notes", "observacoes", "comentarios"),
}


def _normalize_steps_column_name(col: str) -> str:
    cleaned = str(col).strip().lower().replace(" ", "_")
    if cleaned.startswith("com.samsung."):
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned


def _parse_steps_date_safe(value: object) -> date | None:
    """Aceita strings ISO/BR ou timestamps Unix (ms/s) usados pelo Samsung Health."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if num > 1e11:
            return datetime.fromtimestamp(num / 1000.0, tz=timezone.utc).date()
        if num > 1e9:
            return datetime.fromtimestamp(num, tz=timezone.utc).date()

    text = str(value).strip()
    if not text:
        return None
    try:
        num = float(text)
        if num > 1e11:
            return datetime.fromtimestamp(num / 1000.0, tz=timezone.utc).date()
        if num > 1e9:
            return datetime.fromtimestamp(num, tz=timezone.utc).date()
    except ValueError:
        pass

    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date()


def parse_samsung_health_or_generic_steps_csv(file_content: bytes | str) -> list[StepsIn]:
    """Lê CSV de passos do Samsung Health (step_daily_trend / pedometer) ou CSV genérico."""
    text = _decode_content(file_content).strip()
    if not text:
        return []

    lines = text.splitlines()
    first_line_lower = lines[0].strip().lower()
    is_samsung_export = first_line_lower.startswith("com.samsung.")
    skip_rows = 1 if is_samsung_export else 0

    header_idx = 1 if is_samsung_export and len(lines) > 1 else 0
    sep = _detect_separator(lines[header_idx])

    df = pd.read_csv(
        io.StringIO(text),
        skiprows=skip_rows,
        sep=sep,
        index_col=False,
    )
    if df.empty:
        return []

    normalized_cols = {_normalize_steps_column_name(col): col for col in df.columns}
    col_map: dict[str, str] = {}
    for canonical, aliases in STEPS_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_cols:
                col_map[canonical] = normalized_cols[alias]
                break

    source_default = "samsung_health" if is_samsung_export else "csv_import"
    parsed_by_date: dict[date, StepsIn] = {}

    for _, row in df.iterrows():
        raw: dict[str, object] = {"source": source_default}

        if "step_date" in col_map:
            parsed_date = _parse_steps_date_safe(row[col_map["step_date"]])
            if parsed_date is not None:
                raw["step_date"] = parsed_date

        if "steps_count" in col_map:
            steps_val = _parse_float_safe(row[col_map["steps_count"]])
            if steps_val is not None:
                raw["steps_count"] = int(round(steps_val))

        if "goal_steps" in col_map:
            goal_val = _parse_float_safe(row[col_map["goal_steps"]])
            if goal_val is not None and goal_val >= 100:
                raw["goal_steps"] = int(round(goal_val))

        if "distance_km" in col_map:
            dist_val = _parse_float_safe(row[col_map["distance_km"]])
            if dist_val is not None and dist_val >= 0:
                raw["distance_km"] = round(dist_val / 1000.0, 2) if dist_val > 100 else round(dist_val, 2)

        if "active_minutes" in col_map:
            act_val = _normalize_duration_minutes(_parse_float_safe(row[col_map["active_minutes"]]))
            if act_val is not None:
                raw["active_minutes"] = act_val

        if "calories_kcal" in col_map:
            cal_val = _parse_float_safe(row[col_map["calories_kcal"]])
            if cal_val is not None and cal_val >= 0:
                raw["calories_kcal"] = round(cal_val, 1)

        if "floors_climbed" in col_map:
            floors_val = _parse_float_safe(row[col_map["floors_climbed"]])
            if floors_val is not None and floors_val >= 0:
                raw["floors_climbed"] = int(round(floors_val))

        if "notes" in col_map:
            note_val = row[col_map["notes"]]
            if pd.notna(note_val) and str(note_val).strip():
                raw["notes"] = str(note_val).strip()

        try:
            item = StepsIn.model_validate(raw)
        except ValueError:
            continue

        # No Samsung Health step_daily_trend há linhas por dispositivo + consolidado; mantém o maior total do dia
        if item.step_date is not None:
            existing = parsed_by_date.get(item.step_date)
            if existing is None or (item.steps_count or 0) >= (existing.steps_count or 0):
                parsed_by_date[item.step_date] = item

    return sorted(parsed_by_date.values(), key=lambda x: x.step_date or date.min)


def parse_steps_json(file_content: bytes | str) -> list[StepsIn]:
    """Lê arquivo JSON (objeto único, lista ou {'records': [...]}) em StepsIn."""
    text = _decode_content(file_content).strip()
    if not text:
        return []
    payload = json.loads(text)
    if isinstance(payload, dict):
        items = payload["records"] if "records" in payload else [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("Formato JSON inválido: esperado objeto ou lista de registros.")
    return [StepsIn.model_validate(item) for item in items]


def generate_steps_csv_template() -> str:
    """Retorna um CSV modelo para importação de passos diários."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "step_date",
            "steps_count",
            "goal_steps",
            "distance_km",
            "active_minutes",
            "calories_kcal",
            "notes",
        ]
    )
    writer.writerow(
        [
            "2026-11-16",
            "10450",
            "10000",
            "7.94",
            "82",
            "418",
            "Caminhada + rodagem leve",
        ]
    )
    return buffer.getvalue()


def generate_sample_steps_data(days: int = 30) -> list[StepsIn]:
    """Gera uma série sintética de 30 dias de passos coerente com os dados de sono."""
    rng = random.Random(42)
    today = datetime.now(ZoneInfo(settings.TIMEZONE)).date()
    records: list[StepsIn] = []

    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        is_weekend = day.weekday() in (5, 6)

        if offset in (2, 1):
            steps_val = int(rng.uniform(5500, 7400))
            active_min = round(rng.uniform(35, 52), 1)
            note = "Dia mais leve / cansado (simulado)"
        elif is_weekend:
            steps_val = int(rng.uniform(13200, 16500))
            active_min = round(rng.uniform(95, 130), 1)
            note = "Dia de rodagem longa (simulado)"
        else:
            steps_val = int(rng.uniform(7800, 11800))
            active_min = round(rng.uniform(55, 88), 1)
            note = "Rotina diária Samsung Health (simulado)"

        records.append(
            StepsIn(
                step_date=day,
                steps_count=steps_val,
                goal_steps=settings.STEPS_TARGET,
                distance_km=round(steps_val * 0.00076, 2),
                active_minutes=active_min,
                calories_kcal=round(steps_val * 0.04, 1),
                floors_climbed=rng.randint(4, 16),
                source="samsung_health",
                notes=note,
            )
        )

    return records
