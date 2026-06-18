from django.core.management.base import BaseCommand

from explanation.services.factory import build_explanation_snapshot


class Command(BaseCommand):
    help = 'macro と basecalc の保存済み出力から ExplanationSnapshot を作成する'

    def handle(self, *args, **options):
        snapshot = build_explanation_snapshot(save=True)
        self.stdout.write(
            self.style.SUCCESS(
                f'saved ExplanationSnapshot {snapshot.as_of.isoformat()} '
                f'{snapshot.final_label} {snapshot.confidence_grade}/{snapshot.confidence_score}'
            )
        )
