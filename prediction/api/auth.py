"""Prediction の認証層。

- API キー認証: X-API-KEY ヘッダで保護（外部から JSON API を叩く用途）
- ハイブリッドセッション認証: パスフレーズ入力 → 6 時間有効なセッションフラグ
  を立てる（ページの更新ボタンを保護する用途）
"""

import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional

from django.http import JsonResponse


SESSION_AUTH_KEY = 'prediction_auth_until'
SESSION_DURATION_SEC = 6 * 60 * 60  # 6 時間


def get_api_key() -> Optional[str]:
    value = os.getenv('PREDICTION_API_KEY')
    if value:
        value = value.strip()
    return value or None


def get_passphrase() -> Optional[str]:
    value = os.getenv('PREDICTION_AUTH_PASSPHRASE')
    if value:
        value = value.strip()
    return value or None


def require_api_key(view_func):
    """API エンドポイント用のデコレータ。X-API-KEY ヘッダを検証する。"""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        expected = get_api_key()
        if not expected:
            return JsonResponse(
                {'error': 'PREDICTION_API_KEY is not configured'},
                status=503,
            )
        provided = request.META.get('HTTP_X_API_KEY', '').strip()
        if not provided or not hmac.compare_digest(provided, expected):
            return JsonResponse({'error': 'unauthorized'}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


def verify_passphrase(provided: str) -> bool:
    expected = get_passphrase()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.strip(), expected)


def grant_session(request):
    """セッションに認証フラグを立て、有効期限を保存する。"""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_DURATION_SEC)
    request.session[SESSION_AUTH_KEY] = expires_at.isoformat()


def has_valid_session(request) -> bool:
    raw = request.session.get(SESSION_AUTH_KEY)
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        request.session.pop(SESSION_AUTH_KEY, None)
        return False
    return True


def session_remaining_seconds(request) -> int:
    raw = request.session.get(SESSION_AUTH_KEY)
    if not raw:
        return 0
    try:
        expires_at = datetime.fromisoformat(raw)
    except ValueError:
        return 0
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))
