from django.core.management.base import BaseCommand

from basecalc.operations import refresh_basecalc_data


class Command(BaseCommand):
    help = "Refresh basecalc market data, save world model prediction, and settle due outcomes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-save-prediction",
            action="store_true",
            help="Refresh data and settle outcomes without saving a new prediction.",
        )
        parser.add_argument(
            "--no-lock",
            action="store_true",
            help="Run without the basecalc refresh lock.",
        )

    def handle(self, *args, **options):
        result = refresh_basecalc_data(
            save=not options["no_save_prediction"],
            use_lock=not options["no_lock"],
        )
        if result.get("updated"):
            self.stdout.write(
                self.style.SUCCESS(
                    "basecalc refresh complete: "
                    f"price={result.get('price')}, "
                    f"state={result.get('state_key')}, "
                    f"direction={result.get('direction')}, "
                    f"prediction_saved={result.get('prediction_saved')}, "
                    f"outcomes={result.get('outcomes_created')}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"basecalc refresh skipped: {result.get('skipped_reason')}"
                )
            )
