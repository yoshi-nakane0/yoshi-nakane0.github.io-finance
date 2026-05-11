import csv
from datetime import datetime, date
from pathlib import Path

from django.db import transaction

from earning.models import EarningsEvent


def _fmt(value):
    if value is None:
        return ''
    return str(value).strip()


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def _apply(event, q_prefix, row):
    event.eps_forecast = _fmt(row.get(f'{q_prefix}_eps_forecast'))
    event.surp_eps_current = _fmt(row.get(f'{q_prefix}_eps_surprise'))
    event.sales_forecast = _fmt(row.get(f'{q_prefix}_sales_forecast'))
    event.surp_current = _fmt(row.get(f'{q_prefix}_sales_surprise'))
    event.save(update_fields=[
        'eps_forecast', 'surp_eps_current',
        'sales_forecast', 'surp_current',
        'updated_at',
    ])


def sync_eps_sales_csv_to_db(csv_path, today=None):
    path = Path(csv_path)
    if not path.exists():
        return 0, 0, 0

    today = today or date.today()
    updated_q0 = updated_q1 = skipped = 0

    with path.open('r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        with transaction.atomic():
            for row in reader:
                symbol = (row.get('symbol') or '').strip()
                if not symbol:
                    continue
                q0_date = _parse_date(row.get('date'))
                if q0_date is None:
                    skipped += 1
                    continue
                if q0_date < today:
                    event = (
                        EarningsEvent.objects
                        .filter(stock__symbol=symbol, event_date=q0_date)
                        .first()
                    )
                    if event is None:
                        skipped += 1
                        continue
                    _apply(event, 'q0', row)
                    updated_q0 += 1
                else:
                    event = (
                        EarningsEvent.objects
                        .filter(stock__symbol=symbol, event_date__lt=today)
                        .order_by('-event_date')
                        .first()
                    )
                    if event is None:
                        skipped += 1
                        continue
                    _apply(event, 'q1', row)
                    updated_q1 += 1

    return updated_q0, updated_q1, skipped
