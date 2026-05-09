import csv
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from earning.models import EarningsEvent, Stock


VALID_TRINARY = {'up', 'flat', 'down'}
VALID_INTERP = {'bullish', 'neutral', 'bearish'}


def _parse_float(value):
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _norm_choice(value, choices, default):
    if not value:
        return default
    text = str(value).strip().lower()
    return text if text in choices else default


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text or text == '決算日未定':
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = 'Import the legacy earnings CSV into Stock + EarningsEvent (idempotent upsert).'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to data.csv')

    def handle(self, *args, **options):
        path = Path(options['csv_path'])
        if not path.exists():
            raise CommandError(f'CSV not found: {path}')

        created_stocks = updated_stocks = 0
        created_events = updated_events = 0

        with path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            with transaction.atomic():
                for row in reader:
                    symbol = (row.get('symbol') or '').strip()
                    market = (row.get('market') or '').strip()
                    company = (row.get('company') or '').strip()
                    fiscal_period = (row.get('fiscal_period') or '').strip()
                    if not symbol or not market or not company or not fiscal_period:
                        continue

                    stock_defaults = {
                        'company': company,
                        'industry': (row.get('industry') or '').strip(),
                        'theme': (row.get('theme') or '').strip(),
                        'watch_tier': (row.get('watch_tier') or '').strip(),
                        'watch_role': (row.get('watch_role') or '').strip(),
                        'nikkei_weight': _parse_float(row.get('nikkei_weight')),
                    }
                    stock, stock_created = Stock.objects.update_or_create(
                        symbol=symbol, market=market, defaults=stock_defaults,
                    )
                    if stock_created:
                        created_stocks += 1
                    else:
                        updated_stocks += 1

                    past = [
                        _parse_float(row.get('past_q1')),
                        _parse_float(row.get('past_q2')),
                        _parse_float(row.get('past_q3')),
                        _parse_float(row.get('past_q4')),
                    ]

                    event_defaults = {
                        'event_date': _parse_date(row.get('date')),
                        'fundamental': _norm_choice(row.get('Fundamental'), VALID_TRINARY, 'flat'),
                        'direction': _norm_choice(row.get('Direction'), VALID_TRINARY, 'flat'),
                        'sentiment': _norm_choice(row.get('Sentiment'), VALID_TRINARY, 'flat'),
                        'risk_value': _parse_float(row.get('Risk')),
                        'eps_forecast': (row.get('eps_forecast') or '').strip(),
                        'eps_4q_ago': (row.get('eps_4q_ago') or '').strip(),
                        'eps_current': (row.get('eps_current') or '').strip(),
                        'eps_4q_prior_period': (row.get('eps_4q_prior_period') or '').strip(),
                        'surp_eps_4q_ago': (row.get('surp_eps_4q_ago') or '').strip(),
                        'surp_eps_current': (row.get('surp_eps_current') or '').strip(),
                        'surp_eps_4q_prior_period': (row.get('surp_eps_4q_prior_period') or '').strip(),
                        'sales_forecast': (row.get('sales_forecast') or '').strip(),
                        'sales_4q_ago': (row.get('sales_4q_ago') or '').strip(),
                        'sales_current': (row.get('sales_current') or '').strip(),
                        'sales_4q_prior_period': (row.get('sales_4q_prior_period') or '').strip(),
                        'surp_4q_ago': (row.get('surp_4q_ago') or '').strip(),
                        'surp_current': (row.get('surp_current') or '').strip(),
                        'surp_4q_prior_period': (row.get('surp_4q_prior_period') or '').strip(),
                        'theme_score': _parse_float(row.get('theme_score')),
                        'gross_margin': _parse_float(row.get('gross_margin')),
                        'operating_margin': _parse_float(row.get('operating_margin')),
                        'relative_strength': _parse_float(row.get('relative_strength')),
                        'guidance_revision': _norm_choice(row.get('guidance_revision'), VALID_TRINARY, ''),
                        'reaction_close': _parse_float(row.get('reaction_close')),
                        'reaction_next_day': _parse_float(row.get('reaction_next_day')),
                        'market_interpretation': _norm_choice(row.get('market_interpretation'), VALID_INTERP, ''),
                        'past_reactions': past,
                        'summary': (row.get('summary') or '').strip(),
                    }
                    _, event_created = EarningsEvent.objects.update_or_create(
                        stock=stock, fiscal_period=fiscal_period, defaults=event_defaults,
                    )
                    if event_created:
                        created_events += 1
                    else:
                        updated_events += 1

        self.stdout.write(self.style.SUCCESS(
            f'Stocks created={created_stocks} updated={updated_stocks} | '
            f'Events created={created_events} updated={updated_events}'
        ))
