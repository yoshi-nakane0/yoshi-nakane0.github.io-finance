from django.core.management.base import BaseCommand

from basecalc.persistence import export_basecalc_history


class Command(BaseCommand):
    help = "Export basecalc prediction history to JSON."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--limit-predictions", type=int, default=5000)

    def handle(self, *args, **options):
        result = export_basecalc_history(
            options["output"],
            limit_predictions=options["limit_predictions"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "basecalc history exported: "
                f"predictions={result['predictions']}, "
                f"outcomes={result['outcomes']}, "
                f"market_bars={result['market_bars']}, "
                f"path={result['output_path']}"
            )
        )
