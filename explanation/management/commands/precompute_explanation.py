from django.core.management.base import BaseCommand

from explanation.services.factory import build_explanation_snapshot
from explanation.services.static_snapshot import write_static_explanation_snapshot


class Command(BaseCommand):
    help = 'macro と basecalc の保存済み出力から ExplanationSnapshot を作成する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='explanation/data/latest_snapshot.json',
            help='Explanation 表示用JSONの出力先',
        )
        parser.add_argument(
            '--no-export-json',
            action='store_true',
            help='DB保存のみ行い、表示用JSONを出力しない',
        )

    def handle(self, *args, **options):
        snapshot = build_explanation_snapshot(save=True)
        if not options['no_export_json']:
            write_static_explanation_snapshot(snapshot, options['output'])
        self.stdout.write(
            self.style.SUCCESS(
                f'saved ExplanationSnapshot {snapshot.as_of.isoformat()} '
                f'{snapshot.final_label} {snapshot.confidence_grade}/{snapshot.confidence_score}'
            )
        )
