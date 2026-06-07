from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from basecalc.daily_sync import sync_nikkei_futures_daily
from basecalc.persistence import export_basecalc_history


class Command(BaseCommand):
    help = "Sync Nikkei futures daily bars from the free 225navi reference source."

    def add_arguments(self, parser):
        parser.add_argument("--start", help="Start date in YYYY-MM-DD format.")
        parser.add_argument("--end", help="End date in YYYY-MM-DD format.")
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="Update existing MarketBar rows for matching dates.",
        )
        parser.add_argument(
            "--export-history",
            action="store_true",
            help="Export basecalc history JSON after sync.",
        )
        parser.add_argument(
            "--export-path",
            default="basecalc/data/basecalc_history.json",
            help="Path for exported basecalc history JSON.",
        )

    def handle(self, *args, **options):
        start = _parse_date_option(options.get("start"), "start")
        end = _parse_date_option(options.get("end"), "end")
        result = sync_nikkei_futures_daily(
            start=start,
            end=end,
            update_existing=options["update_existing"],
        )
        exported = False
        if options["export_history"]:
            export_basecalc_history(options["export_path"])
            exported = True
        self.stdout.write(
            self.style.SUCCESS(
                "nikkei futures daily sync complete: "
                f"status={result.get('sync_status')}, "
                f"source={result.get('source') or 'none'}, "
                f"attempts={_format_attempts(result.get('attempts'))}, "
                f"fetched={result.get('rows_fetched')}, "
                f"created={result.get('rows_created')}, "
                f"updated={result.get('rows_updated')}, "
                f"snapshot_source={result.get('snapshot_source') or 'none'}, "
                f"snapshot_fetched_at={result.get('snapshot_fetched_at') or 'none'}, "
                f"price={result.get('price')}, "
                f"readiness={result.get('readiness_level')}, "
                f"exported={exported}"
            )
        )


def _parse_date_option(value, label):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Invalid {label} date: {value}") from exc


def _format_attempts(attempts):
    parts = []
    for attempt in attempts or []:
        source = attempt.get("source") or "unknown"
        fetched = attempt.get("rows", 0)
        details = ";".join(attempt.get("details") or [])
        suffix = f"[{details}]" if details else ""
        parts.append(f"{source}:fetched={fetched}{suffix}")
    return ",".join(parts) or "none"
