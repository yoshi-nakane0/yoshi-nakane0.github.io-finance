import logging
import os

from django.conf import settings
from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import ExplanationSnapshot
from .services.factory import build_explanation_snapshot
from .services.freshness import build_explanation_refresh_status
from .services.readiness_score import build_readiness_score
from .services.serializer import snapshot_to_api, snapshot_to_view
from .services.static_snapshot import load_static_explanation_snapshot
from .services.validation_engine import build_static_trade_validation_summary, build_trade_validation_summary


logger = logging.getLogger(__name__)


def _latest_or_preview(price_override=None):
    if _should_use_static_explanation_snapshot():
        static_snapshot = load_static_explanation_snapshot()
        if static_snapshot is None:
            raise RuntimeError(
                '保存済みExplanation JSONがありません。precompute_explanationで生成してください。'
            )
        return static_snapshot, False

    if price_override is not None:
        return build_explanation_snapshot(
            save=False,
            basecalc_price_override=price_override,
        ), True
    try:
        snapshot = ExplanationSnapshot.objects.order_by('-as_of', '-created_at').first()
    except (OperationalError, ProgrammingError):
        logger.warning(
            'explanation snapshot table is unavailable; rendering preview from source data',
        )
        snapshot = None
    if snapshot is not None:
        refresh_status = build_explanation_refresh_status(snapshot)
        if refresh_status.get('needs_refresh'):
            return build_explanation_snapshot(save=False), True
        return snapshot, False
    try:
        return build_explanation_snapshot(save=False), True
    except Exception:
        logger.exception('failed to build explanation preview; falling back to static snapshot')
        static_snapshot = load_static_explanation_snapshot()
        if static_snapshot is not None:
            return static_snapshot, False
        raise


def _should_use_static_explanation_snapshot():
    return _is_serverless_runtime() or not settings.DEBUG


def index(request):
    price_override = _manual_price_from_request(request)
    snapshot, is_preview = _latest_or_preview(price_override)
    context = snapshot_to_view(snapshot)
    context['trade_validation_summary'] = _safe_trade_validation_summary()
    context['readiness_score'] = build_readiness_score(snapshot, context['trade_validation_summary'])
    context['is_preview'] = is_preview
    context['refresh_status'] = build_explanation_refresh_status(snapshot)
    context['can_precompute_explanation'] = _can_precompute_explanation(request)
    return render(request, 'explanation/index.html', context)


def audit(request):
    snapshot, is_preview = _latest_or_preview()
    context = snapshot_to_view(snapshot)
    context['trade_validation_summary'] = _safe_trade_validation_summary()
    context['readiness_score'] = build_readiness_score(snapshot, context['trade_validation_summary'])
    context['is_preview'] = is_preview
    context['score_breakdown'] = snapshot.score_breakdown or {}
    context['source_snapshots'] = snapshot.source_snapshots or {}
    return render(request, 'explanation/audit.html', context)


def latest_api(request):
    snapshot, _is_preview = _latest_or_preview()
    return JsonResponse(snapshot_to_api(snapshot))


@require_POST
def precompute(request):
    if not _can_precompute_explanation(request):
        return HttpResponseForbidden('Forbidden')
    try:
        snapshot = build_explanation_snapshot(save=True)
    except Exception as exc:
        logger.exception('failed to precompute explanation snapshot')
        messages.error(request, f'Explanation の再作成に失敗しました: {exc}')
    else:
        logger.info('explanation snapshot precomputed: %s', snapshot.as_of.isoformat())
        messages.success(request, 'Explanation を再作成しました。')
    return redirect('explanation:index')


def _can_precompute_explanation(request):
    if request.user.is_authenticated and request.user.is_staff:
        return True
    return _is_local_request(request)


def _is_local_request(request):
    if not settings.DEBUG:
        return False
    host = request.get_host().split(':', 1)[0].strip('[]')
    remote_addr = request.META.get('REMOTE_ADDR')
    return host in {'localhost', '127.0.0.1', '::1'} or remote_addr in {
        '127.0.0.1',
        '::1',
    }


def _manual_price_from_request(request):
    value = request.GET.get('price')
    if not value:
        return None
    cleaned = str(value).replace(',', '').strip()
    try:
        price = int(float(cleaned))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _safe_trade_validation_summary():
    if _should_use_static_explanation_snapshot():
        return build_static_trade_validation_summary()
    try:
        return build_trade_validation_summary(include_static=True)
    except (OperationalError, ProgrammingError):
        return build_static_trade_validation_summary()


def _is_serverless_runtime():
    return any(
        os.getenv(name)
        for name in ('VERCEL', 'AWS_LAMBDA_FUNCTION_NAME', 'LAMBDA_TASK_ROOT')
    )
