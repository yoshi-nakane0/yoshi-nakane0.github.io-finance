"""本番で保存済みのデータファイルをローカルへ同期する。"""

import json
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
)


class ProductionDataSyncError(Exception):
    """本番データ同期に失敗した場合の例外。"""


def download_url(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def discover_data_paths(base_dir=None):
    root = Path(base_dir or settings.BASE_DIR)
    paths = []
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
    if relative_path.startswith("basecalc/data/"):
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

    for relative_path in target_paths:
        url = source_url_for_path(relative_path)
        try:
            content = downloader(url)
            if relative_path.endswith(".json"):
                json.loads(content.decode("utf-8"))
        except Exception as exc:
            raise ProductionDataSyncError(
                f"{relative_path} の取得に失敗しました: {exc}"
            ) from exc
        downloads.append((relative_path, content))

    updated = []
    unchanged = []
    mirrored = []
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

    return {
        "updated": updated,
        "unchanged": unchanged,
        "mirrored": mirrored,
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "mirrored_count": len(mirrored),
    }


def _staticfiles_alias_path(root, relative_path):
    if not relative_path.startswith("static/"):
        return None
    return root / "staticfiles" / relative_path.removeprefix("static/")


def _write_bytes_atomic(path, content):
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)
