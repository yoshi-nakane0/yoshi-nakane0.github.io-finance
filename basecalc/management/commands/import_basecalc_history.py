from django.core.management.base import BaseCommand, CommandError

from basecalc.persistence import evaluate_imported_history, import_basecalc_history


class Command(BaseCommand):
    help = "Import basecalc prediction history from JSON."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument(
            "--evaluate-due",
            action="store_true",
            help="Evaluate due predictions after import.",
        )

    def handle(self, *args, **options):
        try:
            result = import_basecalc_history(options["input"])
        except Exception as exc:
            raise CommandError(f"basecalc history import failed: {exc}") from exc
        if result.get("skipped"):
            self.stdout.write(
                self.style.WARNING(
                    f"basecalc history import skipped: {result.get('reason')}"
                )
            )
            return
        evaluated = evaluate_imported_history() if options["evaluate_due"] else 0
        self.stdout.write(
            self.style.SUCCESS(
                "basecalc history imported: "
                f"predictions_created={result['predictions_created']}, "
                f"outcomes_created={result['outcomes_created']}, "
                f"market_bars_created={result['market_bars_created']}, "
                f"market_bars_updated={result.get('market_bars_updated', 0)}, "
                f"evaluated={evaluated}"
            )
        )
