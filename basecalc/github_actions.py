import os

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


CACHE_KEY_REFRESH_WORKFLOW_STATE = "basecalc_refresh_workflow_state"
REFRESH_WORKFLOW_STATE_TTL_SEC = 300
GITHUB_API_BASE_URL = "https://api.github.com"
REQUEST_TIMEOUT_SEC = (5, 15)
RUNNING_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}


def dispatch_refresh_workflow():
    state = get_cached_refresh_workflow_state()
    if state.get("status") == "running":
        return {
            "ok": False,
            "skipped": True,
            "state": state,
        }

    config = refresh_workflow_config()
    if not config["repository"] or not config["token"]:
        state = _store_state(
            "failure",
            "GitHub Actions 起動用の設定がありません",
        )
        return {"ok": False, "state": state}

    response = requests.post(
        workflow_dispatch_url(config),
        headers=github_headers(config["token"]),
        json={"ref": config["ref"]},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    if response.status_code == 204:
        state = _store_state("running", "GitHub Actions 実行中")
        return {"ok": True, "state": state}

    state = _store_state(
        "failure",
        "GitHub Actions 起動に失敗しました",
        detail=(response.text or "")[:240],
    )
    return {"ok": False, "state": state}


def get_cached_refresh_workflow_state():
    cached = cache.get(CACHE_KEY_REFRESH_WORKFLOW_STATE)
    if isinstance(cached, dict):
        return cached
    return {
        "status": "idle",
        "message": "GitHub Actions 待機中",
        "updated_at": "",
        "detail": "",
    }


def get_refresh_workflow_state():
    cached = cache.get(CACHE_KEY_REFRESH_WORKFLOW_STATE)
    if isinstance(cached, dict):
        return cached

    config = refresh_workflow_config()
    if not config["repository"]:
        return {
            "status": "unconfigured",
            "message": "GitHub Actions 状態未設定",
            "updated_at": "",
            "detail": "",
        }

    try:
        response = requests.get(
            workflow_runs_url(config),
            headers=github_headers(config["token"]) if config["token"] else github_headers(),
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        run = (response.json().get("workflow_runs") or [None])[0]
    except (requests.RequestException, ValueError, AttributeError, IndexError):
        return {
            "status": "unknown",
            "message": "GitHub Actions 状態確認不可",
            "updated_at": "",
            "detail": "",
        }

    if not run:
        return {
            "status": "idle",
            "message": "GitHub Actions 履歴なし",
            "updated_at": "",
            "detail": "",
        }

    state = state_from_workflow_run(run)
    cache.set(CACHE_KEY_REFRESH_WORKFLOW_STATE, state, timeout=60)
    return state


def state_from_workflow_run(run):
    status = run.get("status") or ""
    conclusion = run.get("conclusion") or ""
    updated_at = run.get("updated_at") or run.get("created_at") or ""
    html_url = run.get("html_url") or ""
    if status in RUNNING_STATUSES:
        return {
            "status": "running",
            "message": "GitHub Actions 実行中",
            "updated_at": updated_at,
            "detail": "",
            "url": html_url,
        }
    if conclusion == "success":
        return {
            "status": "success",
            "message": "GitHub Actions 成功",
            "updated_at": updated_at,
            "detail": "",
            "url": html_url,
        }
    if conclusion:
        return {
            "status": "failure",
            "message": "GitHub Actions 失敗",
            "updated_at": updated_at,
            "detail": conclusion,
            "url": html_url,
        }
    return {
        "status": "unknown",
        "message": "GitHub Actions 状態確認中",
        "updated_at": updated_at,
        "detail": "",
        "url": html_url,
    }


def refresh_workflow_config():
    return {
        "repository": (
            getattr(settings, "BASECALC_REFRESH_WORKFLOW_REPOSITORY", "")
            or os.getenv("BASECALC_REFRESH_WORKFLOW_REPOSITORY", "")
            or os.getenv("GITHUB_REPOSITORY", "")
        ).strip(),
        "workflow_file": (
            getattr(settings, "BASECALC_REFRESH_WORKFLOW_FILE", "")
            or os.getenv("BASECALC_REFRESH_WORKFLOW_FILE", "")
            or "refresh-basecalc.yml"
        ).strip(),
        "ref": (
            getattr(settings, "BASECALC_REFRESH_WORKFLOW_REF", "")
            or os.getenv("BASECALC_REFRESH_WORKFLOW_REF", "")
            or "main"
        ).strip(),
        "token": (
            getattr(settings, "BASECALC_REFRESH_WORKFLOW_TOKEN", "")
            or os.getenv("BASECALC_REFRESH_WORKFLOW_TOKEN", "")
            or os.getenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "")
        ).strip(),
    }


def workflow_dispatch_url(config):
    return (
        f"{GITHUB_API_BASE_URL}/repos/{config['repository']}/actions/workflows/"
        f"{config['workflow_file']}/dispatches"
    )


def workflow_runs_url(config):
    return (
        f"{GITHUB_API_BASE_URL}/repos/{config['repository']}/actions/workflows/"
        f"{config['workflow_file']}/runs?branch={config['ref']}&per_page=1"
    )


def github_headers(token=""):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _store_state(status, message, detail=""):
    state = {
        "status": status,
        "message": message,
        "updated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M"),
        "detail": detail,
    }
    cache.set(
        CACHE_KEY_REFRESH_WORKFLOW_STATE,
        state,
        timeout=REFRESH_WORKFLOW_STATE_TTL_SEC,
    )
    return state
