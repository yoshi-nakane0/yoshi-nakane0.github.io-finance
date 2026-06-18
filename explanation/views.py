import logging

from django.db import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render

from .models import ExplanationSnapshot
from .services.factory import build_explanation_snapshot
from .services.serializer import snapshot_to_api, snapshot_to_view


logger = logging.getLogger(__name__)


def _latest_or_preview():
    try:
        snapshot = ExplanationSnapshot.objects.order_by('-as_of', '-created_at').first()
    except (OperationalError, ProgrammingError):
        logger.warning(
            'explanation snapshot table is unavailable; rendering preview from source data',
        )
        snapshot = None
    if snapshot is not None:
        return snapshot, False
    return build_explanation_snapshot(save=False), True


def index(request):
    snapshot, is_preview = _latest_or_preview()
    context = snapshot_to_view(snapshot)
    context['is_preview'] = is_preview
    return render(request, 'explanation/index.html', context)


def audit(request):
    snapshot, is_preview = _latest_or_preview()
    context = snapshot_to_view(snapshot)
    context['is_preview'] = is_preview
    context['score_breakdown'] = snapshot.score_breakdown or {}
    context['source_snapshots'] = snapshot.source_snapshots or {}
    return render(request, 'explanation/audit.html', context)


def latest_api(request):
    snapshot, _is_preview = _latest_or_preview()
    return JsonResponse(snapshot_to_api(snapshot))
