import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


FORBIDDEN_DIRECTIONAL_STATES = {
    "bull_trend_continuation",
    "dip_buy",
    "short_covering",
    "bull_impulse",
    "bear_trend_continuation",
    "return_sell",
    "bear_impulse",
}


class Command(BaseCommand):
    help = "Validate basecalc history reliability fields."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="basecalc/data/basecalc_history.json",
            help="Path to basecalc history JSON.",
        )

    def handle(self, *args, **options):
        path = Path(options["input"])
        if not path.exists():
            raise CommandError(f"history file not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"invalid JSON: {exc}") from exc

        errors = []
        if payload.get("schema") != "basecalc_history_v2":
            errors.append("schema must be basecalc_history_v2")

        predictions = payload.get("predictions") or []
        if predictions:
            latest = predictions[0]
            if not latest.get("readiness_level"):
                errors.append("latest prediction missing readiness_level")
            if not latest.get("instrument_key"):
                errors.append("latest prediction missing instrument_key")

        for index, prediction in enumerate(predictions):
            readiness = prediction.get("readiness_level") or "blocked"
            direction = prediction.get("direction")
            state_key = prediction.get("state_key")
            state_label = prediction.get("state_label") or ""
            symbol = (prediction.get("source_symbol") or (prediction.get("features") or {}).get("symbol") or "").upper()
            instrument_key = prediction.get("instrument_key")

            if readiness in {"blocked", "limited"} and (
                direction in {"up", "down"}
                or state_key in FORBIDDEN_DIRECTIONAL_STATES
                or any(word in state_label for word in ("上昇継続", "下落継続", "押し目買い", "戻り売り"))
            ):
                errors.append(f"prediction[{index}] has directional state while {readiness}")
            if symbol == "^NKX" and instrument_key == "cme_nikkei_futures":
                errors.append(f"prediction[{index}] stores ^NKX as cme_nikkei_futures")

        if errors:
            raise CommandError("; ".join(errors))
        self.stdout.write(self.style.SUCCESS("basecalc data integrity ok"))
