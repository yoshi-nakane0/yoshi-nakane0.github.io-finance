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
        parser.add_argument(
            "--export-history",
            action="store_true",
            help="Export basecalc history JSON after refresh.",
        )
        parser.add_argument(
            "--export-path",
            default="basecalc/data/basecalc_history.json",
            help="Path for exported basecalc history JSON.",
        )
        parser.add_argument(
            "--export-snapshot-path",
            default="basecalc/data/latest_snapshot.json",
            help="Path for exported basecalc display snapshot JSON.",
        )
        parser.add_argument(
            "--skip-off-hours",
            action="store_true",
            help="Skip refresh during low-value market hours. Initial implementation always runs.",
        )

    def handle(self, *args, **options):
        if options["skip_off_hours"] and should_skip_basecalc_refresh():
            self.stdout.write(self.style.WARNING("basecalc refresh skipped: off_hours"))
            return
        result = refresh_basecalc_data(
            save=not options["no_save_prediction"],
            use_lock=not options["no_lock"],
            export_history=options["export_history"],
            export_path=options["export_path"],
            export_snapshot_path=options["export_snapshot_path"],
        )
        if result.get("updated"):
            self.stdout.write(
                self.style.SUCCESS(
                    "basecalc refresh complete: "
                    f"price={result.get('price')}, "
                    f"state={result.get('state_key')}, "
                    f"direction={result.get('direction')}, "
                    f"prediction_saved={result.get('prediction_saved')}, "
                    f"outcomes={result.get('outcomes_created')}, "
                    f"history_exported={result.get('exported')}, "
                    f"snapshot_exported={result.get('snapshot_exported')}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"basecalc refresh skipped: {result.get('skipped_reason')}"
                )
            )


def should_skip_basecalc_refresh(now=None) -> bool:
    """JST 基準で更新スキップ可否を返す。初期実装は False 固定。"""
    return False
