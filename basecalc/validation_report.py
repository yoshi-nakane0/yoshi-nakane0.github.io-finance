import json
from pathlib import Path

from django.utils import timezone

from .calibration import confidence_calibration_summary
from .outcomes import (
    calibration_summary,
    improvement_insights,
    performance_summary,
    state_performance_summary,
)
from .validation import validation_design_summary


DEFAULT_VALIDATION_REPORT_PATH = "basecalc/data/basecalc_validation_report.json"
VALIDATION_REPORT_SCHEMA = "basecalc_validation_report_v1"


def build_validation_report(
    *,
    horizons=("1d", "3d", "5d"),
    instrument_key="cme_nikkei_futures",
    readiness_level="ready",
    is_backtest=True,
    backtest_result=None,
):
    horizon_keys = _normalize_horizons(horizons)
    return {
        "schema": VALIDATION_REPORT_SCHEMA,
        "generated_at": timezone.now().isoformat(),
        "filters": {
            "instrument_key": instrument_key,
            "readiness_level": readiness_level,
            "is_backtest": bool(is_backtest),
        },
        "backtest_run": backtest_result or {},
        "horizons": {
            horizon: _horizon_report(
                horizon,
                instrument_key=instrument_key,
                readiness_level=readiness_level,
                is_backtest=is_backtest,
            )
            for horizon in horizon_keys
        },
    }


def save_validation_report(report, output_path=DEFAULT_VALIDATION_REPORT_PATH):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return {
        "output_path": str(path),
        "horizons": len(report.get("horizons") or {}),
    }


def load_validation_report(input_path=DEFAULT_VALIDATION_REPORT_PATH):
    path = Path(input_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != VALIDATION_REPORT_SCHEMA:
        return None
    return payload


def _horizon_report(
    horizon,
    *,
    instrument_key,
    readiness_level,
    is_backtest,
):
    return {
        "summary": performance_summary(
            horizon=horizon,
            instrument_key=instrument_key,
            readiness_level=readiness_level,
            is_backtest=is_backtest,
        ),
        "state_summaries": state_performance_summary(horizon),
        "calibration_rows": calibration_summary(
            horizon,
            instrument_key=instrument_key,
            readiness_level=readiness_level,
            is_backtest=is_backtest,
        ),
        "confidence_calibration_rows": confidence_calibration_summary(
            horizon,
            instrument_key=instrument_key,
            readiness_level=readiness_level,
            is_backtest=is_backtest,
        ),
        "validation_design": validation_design_summary(
            horizon,
            instrument_key=instrument_key,
            readiness_level=readiness_level,
            is_backtest=is_backtest,
        ),
        "improvement_insights": improvement_insights(horizon),
    }


def _normalize_horizons(horizons):
    if isinstance(horizons, str):
        horizons = horizons.split(",")
    normalized = []
    for horizon in horizons or ():
        value = str(horizon).strip()
        if value in {"1d", "3d", "5d"} and value not in normalized:
            normalized.append(value)
    return normalized or ["1d"]


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
