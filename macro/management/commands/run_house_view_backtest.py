from datetime import date

from django.core.management.base import BaseCommand, CommandError

from macro.services.dashboard_cache import write_static_macro_payload
from macro.services.house_view_backtest import run_house_view_backtest


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'日付は YYYY-MM-DD で指定してください: {value}') from exc


def _parse_horizons(value: str):
    try:
        return tuple(int(item.strip()) for item in value.split(',') if item.strip())
    except ValueError as exc:
        raise CommandError('--horizons は 3,6 のように月数で指定してください。') from exc


class Command(BaseCommand):
    help = 'House View の過去再現Backtestをローカルで実行して要約JSONを出力する'

    def add_arguments(self, parser):
        parser.add_argument('--start', default='2015-01-01')
        parser.add_argument('--end', default=None)
        parser.add_argument('--horizons', default='3,6')
        parser.add_argument(
            '--data-mode',
            default='auto',
            choices=('auto', 'revised_reference', 'point_in_time'),
        )
        parser.add_argument('--max-rows', type=int, default=240)
        parser.add_argument(
            '--output',
            default='static/macro/house_view_backtest.json',
            help='出力先JSONパス',
        )

    def handle(self, *args, **options):
        start = _parse_date(options['start'])
        end = _parse_date(options['end']) if options['end'] else date.today()
        if end < start:
            raise CommandError('--end は --start 以降の日付にしてください。')

        payload = run_house_view_backtest(
            start=start,
            end=end,
            horizons=_parse_horizons(options['horizons']),
            data_mode=options['data_mode'],
            max_rows=options['max_rows'],
        )
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported house view backtest: {options['output']}")
        )
