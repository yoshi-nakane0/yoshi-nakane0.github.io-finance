from django.core.management.base import BaseCommand

from basecalc.models import MarketBar
from basecalc.outcomes import save_prediction
from basecalc.world_model import build_world_model


class Command(BaseCommand):
    help = "Recalculate basecalc World Model from saved MarketBar data."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="NIY=F")
        parser.add_argument("--from", dest="date_from")
        parser.add_argument("--to", dest="date_to")
        parser.add_argument("--step", default="1d")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        queryset = MarketBar.objects.filter(
            symbol=options["symbol"],
            timeframe="1d",
        ).order_by("timestamp")
        if options.get("date_from"):
            queryset = queryset.filter(timestamp__date__gte=options["date_from"])
        if options.get("date_to"):
            queryset = queryset.filter(timestamp__date__lte=options["date_to"])
        bars = list(queryset[: options["limit"]])
        if len(bars) < 35:
            self.stdout.write(self.style.WARNING("basecalc backtest skipped: MarketBar不足"))
            return
        created = 0
        for index in range(34, len(bars)):
            window = bars[: index + 1]
            snapshot = _snapshot_from_bars(window, options["symbol"])
            world_model = build_world_model(window[-1].close, snapshot)
            if not options["dry_run"] and save_prediction(world_model):
                created += 1
        self.stdout.write(
            self.style.SUCCESS(
                "basecalc backtest complete: "
                f"evaluated={max(0, len(bars) - 34)}, created={created}, dry_run={options['dry_run']}"
            )
        )


def _snapshot_from_bars(bars, symbol):
    return {
        "symbol": symbol,
        "source": "market_bar_backtest",
        "price": bars[-1].close,
        "previous_close": bars[-2].close if len(bars) >= 2 else bars[-1].close,
        "change_pct": (
            ((bars[-1].close - bars[-2].close) / bars[-2].close) * 100
            if len(bars) >= 2 and bars[-2].close
            else 0
        ),
        "opens": [bar.open or bar.close for bar in bars],
        "highs": [bar.high or bar.close for bar in bars],
        "lows": [bar.low or bar.close for bar in bars],
        "closes": [bar.close for bar in bars],
        "volumes": [bar.volume or 0 for bar in bars],
        "timestamps": [int(bar.timestamp.timestamp()) for bar in bars],
        "fetched_at": bars[-1].timestamp,
    }
