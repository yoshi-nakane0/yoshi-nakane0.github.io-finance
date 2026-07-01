from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from explanation.services.factory import build_explanation_snapshot
from explanation.services.static_snapshot import append_static_explanation_history, write_static_explanation_snapshot


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
        parser.add_argument(
            '--history-output',
            default='explanation/data/snapshot_history.json',
            help='Explanation 判定履歴JSONの出力先',
        )
        parser.add_argument(
            '--max-history-rows',
            type=int,
            default=500,
            help='判定履歴JSONに残す最大件数',
        )

    def handle(self, *args, **options):
        export_warning = ''
        try:
            snapshot = build_explanation_snapshot(save=True)
        except DatabaseError:
            snapshot = build_explanation_snapshot(save=False)
            export_warning = ' db_save_failed_json_only'
        except Exception as exc:
            raise CommandError(f'Explanation precompute failed: {exc}') from exc
        if not options['no_export_json']:
            write_static_explanation_snapshot(snapshot, options['output'])
            history_result = append_static_explanation_history(
                snapshot,
                options['history_output'],
                max_rows=options['max_history_rows'],
            )
        else:
            history_result = {'added': False, 'count': 0}
        decision = snapshot.trade_decision or {}
        self.stdout.write(
            self.style.SUCCESS(
                f'saved ExplanationSnapshot {snapshot.as_of.isoformat()} '
                f'{snapshot.final_label} {snapshot.confidence_grade}/{snapshot.confidence_score} '
                f"side={decision.get('selected_side') or 'N/A'} "
                f"type={decision.get('decision_type') or 'N/A'} "
                f"confidence={decision.get('confidence_score', snapshot.confidence_score)} "
                f"history_added={history_result.get('added')} history_count={history_result.get('count')}"
                f"{export_warning}"
            )
        )
