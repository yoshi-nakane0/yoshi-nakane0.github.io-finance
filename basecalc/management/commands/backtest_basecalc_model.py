import json

from django.core.management.base import BaseCommand

from basecalc.backtesting import run_basecalc_backtest


class Command(BaseCommand):
    help = "Run basecalc backtest from saved MarketBar data."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="NIY=F")
        parser.add_argument("--instrument-key", default="cme_nikkei_futures")
        parser.add_argument("--from", dest="date_from")
        parser.add_argument("--to", dest="date_to")
        parser.add_argument("--timeframe", default="1d")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--min-bars", type=int, default=80)
        parser.add_argument("--write-backtest", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        write = bool(options["write_backtest"]) and not bool(options["dry_run"])
        result = run_basecalc_backtest(
            symbol=options["symbol"],
            instrument_key=options["instrument_key"],
            date_from=options.get("date_from"),
            date_to=options.get("date_to"),
            timeframe=options["timeframe"],
            limit=options.get("limit"),
            min_bars=options["min_bars"],
            write=write,
        )
        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "basecalc backtest complete: "
                f"evaluated={result['evaluated']}, "
                f"created={result['created']}, "
                f"skipped={result['skipped']}, "
                f"write={write}"
            )
        )
