import csv
from datetime import datetime, time, timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from basecalc.instrument import normalize_instrument
from basecalc.models import MarketBar


class Command(BaseCommand):
    help = "Import daily MarketBar rows from a Japanese OHLCV CSV."

    def add_arguments(self, parser):
        parser.add_argument("input")
        parser.add_argument("--symbol", default="NIY=F")
        parser.add_argument("--source", default="csv")
        parser.add_argument("--timeframe", default="1d")
        parser.add_argument("--instrument-key", default="cme_nikkei_futures")
        parser.add_argument("--instrument-type", default="futures")

    def handle(self, *args, **options):
        path = Path(options["input"]).expanduser()
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        instrument = normalize_instrument(options["symbol"], options["source"])
        instrument_key = options["instrument_key"] or instrument["instrument_key"]
        instrument_type = options["instrument_type"] or instrument["instrument_type"]
        rows = []
        skipped = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                parsed = _parse_row(
                    row,
                    symbol=instrument["symbol"] or options["symbol"],
                    source=options["source"],
                    timeframe=options["timeframe"],
                    instrument_key=instrument_key,
                    instrument_type=instrument_type,
                )
                if parsed is None:
                    skipped += 1
                    continue
                rows.append(MarketBar(**parsed))

        if not rows:
            raise CommandError("No importable rows found.")

        MarketBar.objects.bulk_create(
            rows,
            batch_size=500,
            update_conflicts=True,
            update_fields=[
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source",
                "instrument_key",
                "instrument_type",
            ],
            unique_fields=["symbol", "timeframe", "timestamp"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "market bars imported: "
                f"rows={len(rows)}, skipped={skipped}, "
                f"symbol={instrument['symbol'] or options['symbol']}, "
                f"timeframe={options['timeframe']}"
            )
        )


def _parse_row(row, *, symbol, source, timeframe, instrument_key, instrument_type):
    date_value = row.get("日付") or row.get("Date")
    close = _parse_number(row.get("終値") or row.get("Close"))
    if not date_value or close is None:
        return None
    try:
        date = datetime.strptime(date_value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    timestamp = datetime.combine(date, time.min, tzinfo=dt_timezone.utc)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "open": _parse_number(row.get("始値") or row.get("Open")),
        "high": _parse_number(row.get("高値") or row.get("High")),
        "low": _parse_number(row.get("安値") or row.get("Low")),
        "close": close,
        "volume": _parse_volume(row.get("出来高") or row.get("Volume")),
        "source": source,
        "instrument_key": instrument_key,
        "instrument_type": instrument_type,
    }


def _parse_number(value):
    if value is None:
        return None
    value = str(value).strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_volume(value):
    if value is None:
        return None
    value = str(value).strip().replace(",", "")
    if not value:
        return None
    multiplier = 1
    suffix = value[-1:].upper()
    if suffix == "K":
        multiplier = 1_000
        value = value[:-1]
    elif suffix == "M":
        multiplier = 1_000_000
        value = value[:-1]
    elif suffix == "B":
        multiplier = 1_000_000_000
        value = value[:-1]
    try:
        return float(value) * multiplier
    except ValueError:
        return None
