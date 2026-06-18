from django.core.management.base import BaseCommand

from macro.models import MacroScenario
from macro.services.dashboard_cache import write_static_macro_payload


def _market_impact(scenario):
    nikkei_map = {
        'long': 'upside_support',
        'short': 'downside_pressure',
        'neutral': 'neutral',
    }
    return {
        'nikkei_futures': nikkei_map.get(scenario.nikkei_bias, scenario.nikkei_bias),
        'sp500': 'valuation_pressure' if scenario.nikkei_bias == 'short' else 'neutral',
        'usd_jpy': 'rate_sensitive',
    }


def build_scenario_ledger(limit=100):
    rows = []
    scenarios = (
        MacroScenario.objects
        .select_related('run')
        .order_by('-run__as_of', 'name')[:limit]
    )
    for scenario in scenarios:
        as_of = scenario.run.as_of.isoformat()
        rows.append({
            'scenario_id': f'{scenario.name}_{as_of}',
            'as_of': as_of,
            'name': scenario.get_name_display(),
            'probability': scenario.probability,
            'expected_market_impact': _market_impact(scenario),
            'watch_window': '20 trading days',
            'confirmation_rules': scenario.key_drivers,
            'invalidation_rules': scenario.invalidation_triggers,
            'status': 'open',
            'outcome': None,
        })
    return {'scenario_ledger': rows}


class Command(BaseCommand):
    help = 'シナリオ台帳を static JSON として出力する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='static/macro/scenario_ledger.json',
            help='出力先JSONパス',
        )
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        payload = build_scenario_ledger(limit=options['limit'])
        write_static_macro_payload(payload, options['output'])
        self.stdout.write(
            self.style.SUCCESS(f"exported scenario ledger: {options['output']}")
        )
