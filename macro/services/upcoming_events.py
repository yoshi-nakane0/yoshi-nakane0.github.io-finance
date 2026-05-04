"""events モジュールの CSV から直近の重要イベントを読み込む。

events 側のロジックには依存せず CSV を直接読み取る（軽量化目的）。
"""

import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

EVENTS_CSV = Path('static') / 'events' / 'data.csv'
HIGH_IMPACT_LABEL = '★★★'


def _csv_path() -> Path:
    return Path(settings.BASE_DIR) / EVENTS_CSV


def load_upcoming_high_impact_events(
    today: Optional[datetime.date] = None,
    days_ahead: int = 7,
) -> List[dict]:
    """直近 days_ahead 日以内の★★★イベントを返す（直近順）。"""
    path = _csv_path()
    if not path.exists():
        return []

    if today is None:
        from django.utils import timezone
        today = timezone.localdate()
    cutoff = today + timedelta(days=days_ahead)

    items: List[dict] = []
    try:
        with path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = (row.get('date') or '').strip()
                impact = (row.get('impact') or '').strip()
                if not date_str or impact != HIGH_IMPACT_LABEL:
                    continue
                try:
                    event_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    continue
                if event_date < today or event_date > cutoff:
                    continue
                items.append({
                    'date': event_date,
                    'time': (row.get('time') or '').strip(),
                    'currency': (row.get('currency') or '').strip(),
                    'event': (row.get('event') or '').strip(),
                    'impact': impact,
                })
    except Exception:
        logger.exception("Failed to load events CSV at %s", path)
        return []

    items.sort(key=lambda x: (x['date'], x['time']))
    return items
