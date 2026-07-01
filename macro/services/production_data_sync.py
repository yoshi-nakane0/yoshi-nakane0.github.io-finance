"""本番で保存済みのデータファイルをローカルへ同期する。"""

import json
from datetime import date
from pathlib import Path

import requests
from django.conf import settings


PRODUCTION_BASE_URL = "https://yoshi-nakane0-github-io-finance.vercel.app"
GITHUB_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/yoshi-nakane0/"
    "yoshi-nakane0.github.io-finance/main"
)
DATA_PATTERNS = (
    "static/**/*.csv",
    "static/**/*.json",
    "basecalc/data/*.json",
    "explanation/data/*.json",
)
REQUIRED_DATA_PATHS = (
    "static/finance_data_manifest.json",
    "basecalc/data/latest_snapshot.json",
    "basecalc/data/basecalc_status.json",
    "basecalc/data/basecalc_history.json",
    "explanation/data/latest_snapshot.json",
    "explanation/data/trade_outcomes.json",
)
OPTIONAL_DATA_PATHS = {
    "explanation/data/snapshot_history.json",
}


class ProductionDataSyncError(Exception):
    """本番データ同期に失敗した場合の例外。"""


def download_url(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def discover_data_paths(base_dir=None):
    root = Path(base_dir or settings.BASE_DIR)
    paths = list(REQUIRED_DATA_PATHS)
    for pattern in DATA_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file():
                paths.append(path.relative_to(root).as_posix())
    return sorted(set(paths))


def source_url_for_path(
    path,
    *,
    production_base_url=PRODUCTION_BASE_URL,
    github_raw_base_url=GITHUB_RAW_BASE_URL,
):
    relative_path = str(path).replace("\\", "/")
    if relative_path.startswith("static/"):
        return f"{production_base_url.rstrip('/')}/{relative_path}"
    if relative_path.startswith("basecalc/data/") or relative_path.startswith("explanation/data/"):
        return f"{github_raw_base_url.rstrip('/')}/{relative_path}"
    raise ProductionDataSyncError(f"同期対象外のパスです: {relative_path}")


def sync_production_data(
    *,
    base_dir=None,
    paths=None,
    downloader=download_url,
    mirror_staticfiles=True,
):
    root = Path(base_dir or settings.BASE_DIR)
    target_paths = [str(path).replace("\\", "/") for path in (paths or discover_data_paths(root))]
    downloads = []
    skipped_optional = []

    for relative_path in target_paths:
        url = source_url_for_path(relative_path)
        try:
            content = downloader(url)
            if relative_path.endswith(".json"):
                json.loads(content.decode("utf-8"))
        except Exception as exc:
            if relative_path in OPTIONAL_DATA_PATHS and _is_not_found(exc):
                skipped_optional.append(relative_path)
                continue
            raise ProductionDataSyncError(
                f"{relative_path} の取得に失敗しました: {exc}"
            ) from exc
        downloads.append((relative_path, content))

    updated = []
    unchanged = []
    mirrored = []
    forecast_snapshots_imported_count = 0
    basecalc_history_imported_count = 0
    explanation_snapshots_imported_count = 0
    for relative_path, content in downloads:
        local_path = root / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists() and local_path.read_bytes() == content:
            unchanged.append(relative_path)
        else:
            _write_bytes_atomic(local_path, content)
            updated.append(relative_path)

        staticfiles_path = _staticfiles_alias_path(root, relative_path)
        if mirror_staticfiles and staticfiles_path and staticfiles_path.exists():
            if staticfiles_path.read_bytes() != content:
                _write_bytes_atomic(staticfiles_path, content)
                mirrored.append(staticfiles_path.relative_to(root).as_posix())

        if relative_path == "static/macro/forecast_ledger.json":
            forecast_snapshots_imported_count = _import_forecast_ledger(content)
        if relative_path == "basecalc/data/basecalc_history.json":
            basecalc_history_imported_count = _import_basecalc_history(local_path)
        if relative_path == "explanation/data/latest_snapshot.json":
            explanation_snapshots_imported_count = _import_explanation_snapshot(local_path)

    return {
        "updated": updated,
        "unchanged": unchanged,
        "mirrored": mirrored,
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "mirrored_count": len(mirrored),
        "skipped_optional": skipped_optional,
        "forecast_snapshots_imported_count": forecast_snapshots_imported_count,
        "basecalc_history_imported_count": basecalc_history_imported_count,
        "explanation_snapshots_imported_count": explanation_snapshots_imported_count,
    }


def _is_not_found(exc):
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


def _staticfiles_alias_path(root, relative_path):
    if not relative_path.startswith("static/"):
        return None
    return root / "staticfiles" / relative_path.removeprefix("static/")


def _write_bytes_atomic(path, content):
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


def _import_forecast_ledger(content):
    from macro.models import ForecastSnapshot

    payload = json.loads(content.decode("utf-8"))
    rows = payload.get("forecast_ledger") or []
    imported_count = 0

    for row in rows:
        as_of = row.get("as_of")
        model_version = row.get("model_version")
        target = row.get("target")
        horizon = row.get("horizon")
        prediction = row.get("prediction")
        if not all([as_of, model_version, target, horizon]) or prediction is None:
            continue

        metadata = {
            "primary_regime": row.get("primary_regime"),
            "previous_regime": row.get("previous_regime"),
            "direction": row.get("direction"),
            "scenario_id": row.get("scenario_id"),
            "source": "forecast_ledger_sync",
        }
        metadata = {
            key: value for key, value in metadata.items()
            if value is not None
        }
        defaults = {
            "prediction_value": prediction,
            "prediction_interval": row.get("prediction_interval"),
            "features_hash": row.get("features_hash") or "",
            "metadata": metadata,
            "realized_value": row.get("realized_value"),
            "error": row.get("error"),
        }
        ForecastSnapshot.objects.update_or_create(
            as_of_date=date.fromisoformat(as_of),
            model_version=model_version,
            target=target,
            horizon=horizon,
            defaults=defaults,
        )
        imported_count += 1

    return imported_count


def _import_basecalc_history(path):
    from basecalc.persistence import import_basecalc_history

    result = import_basecalc_history(str(path))
    return int(result.get("market_bars_created") or 0) + int(result.get("market_bars_updated") or 0)


def _import_explanation_snapshot(path):
    from explanation.services.static_snapshot import import_static_explanation_snapshot

    _snapshot, created = import_static_explanation_snapshot(path)
    return 1 if created else 0
