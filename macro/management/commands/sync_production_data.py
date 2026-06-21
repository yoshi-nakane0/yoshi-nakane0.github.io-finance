from django.core.management.base import BaseCommand, CommandError

from macro.services.production_data_sync import (
    ProductionDataSyncError,
    discover_data_paths,
    sync_production_data,
)


class Command(BaseCommand):
    help = "本番環境で保存済みのデータファイルをローカルへ同期する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            action="append",
            dest="paths",
            help="同期する相対パス。複数指定できます。未指定なら主要データを全て同期します。",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="同期対象のデータファイル一覧を表示する",
        )
        parser.add_argument(
            "--no-staticfiles-mirror",
            action="store_true",
            help="staticfiles 配下の同名データを更新しない",
        )

    def handle(self, *args, **options):
        if options["list"]:
            for path in discover_data_paths():
                self.stdout.write(path)
            return

        try:
            result = sync_production_data(
                paths=options["paths"],
                mirror_staticfiles=not options["no_staticfiles_mirror"],
            )
        except ProductionDataSyncError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "本番データ同期が完了しました "
                f"(更新: {result['updated_count']}件, "
                f"変更なし: {result['unchanged_count']}件, "
                f"staticfiles反映: {result['mirrored_count']}件)"
            )
        )
        for path in result["updated"]:
            self.stdout.write(f"updated: {path}")
        for path in result["mirrored"]:
            self.stdout.write(f"mirrored: {path}")
