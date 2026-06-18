from django.core.management.base import BaseCommand

from basecalc.backtesting import run_basecalc_backtest
from basecalc.validation_report import (
    DEFAULT_VALIDATION_REPORT_PATH,
    build_validation_report,
    save_validation_report,
)


class Command(BaseCommand):
    help = "Build a saved basecalc validation report for web display."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=DEFAULT_VALIDATION_REPORT_PATH,
            help="Path for the saved validation report JSON.",
        )
        parser.add_argument(
            "--horizons",
            default="1d,3d,5d",
            help="Comma-separated horizons to include, such as 1d,3d,5d.",
        )
        parser.add_argument("--symbol", default="NIY=F")
        parser.add_argument("--instrument-key", default="cme_nikkei_futures")
        parser.add_argument("--readiness-level", default="ready")
        parser.add_argument("--from", dest="date_from")
        parser.add_argument("--to", dest="date_to")
        parser.add_argument("--timeframe", default="1d")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--min-bars", type=int, default=80)
        parser.add_argument(
            "--live",
            action="store_true",
            help="Build the report from live predictions instead of backtest rows.",
        )
        parser.add_argument(
            "--run-backtest",
            action="store_true",
            help="Run the historical backtest before building the saved report.",
        )
        parser.add_argument(
            "--write-backtest",
            action="store_true",
            help="Persist backtest predictions when --run-backtest is used.",
        )

    def handle(self, *args, **options):
        backtest_result = {}
        is_backtest = not bool(options["live"])
        if options["run_backtest"]:
            backtest_result = run_basecalc_backtest(
                symbol=options["symbol"],
                instrument_key=options["instrument_key"],
                date_from=options.get("date_from"),
                date_to=options.get("date_to"),
                timeframe=options["timeframe"],
                limit=options.get("limit"),
                min_bars=options["min_bars"],
                write=bool(options["write_backtest"]),
            )
            is_backtest = True

        report = build_validation_report(
            horizons=options["horizons"],
            instrument_key=options["instrument_key"],
            readiness_level=options["readiness_level"],
            is_backtest=is_backtest,
            backtest_result=backtest_result,
        )
        result = save_validation_report(report, options["output"])
        self.stdout.write(
            self.style.SUCCESS(
                "basecalc validation report saved: "
                f"path={result['output_path']}, "
                f"horizons={result['horizons']}, "
                f"is_backtest={is_backtest}"
            )
        )
