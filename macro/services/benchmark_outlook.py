"""無料公開情報だけを参考ベンチマークとして束ねる。"""

from __future__ import annotations

from django.utils import timezone


PUBLIC_BENCHMARKS = [
    {
        'source_id': 'goldman_public',
        'label': 'Goldman Sachs public outlook',
        'source_type': 'public_research_page',
        'source_scope': 'free_public_pages_only',
        'summary': '公開ページに載る成長、インフレ、政策金利の参考見通し。',
        'can_score_house_view': False,
        'usage': '参考ベンチマーク。House Viewの正解判定やモデル検証の代替には使わない。',
    },
    {
        'source_id': 'fomc_sep',
        'label': 'FOMC SEP',
        'source_type': 'official_public_projection',
        'source_scope': 'free_public_official_release',
        'summary': 'Fed参加者の政策金利、成長、失業率、インフレ見通し。',
        'can_score_house_view': False,
        'usage': '政策パスの市場前提との差を確認する参考値。',
    },
    {
        'source_id': 'fedwatch_public',
        'label': 'CME FedWatch public probabilities',
        'source_type': 'public_market_implied_probability',
        'source_scope': 'free_public_page',
        'summary': '次回FOMCの市場織り込みを確認する参考値。',
        'can_score_house_view': False,
        'usage': '政策反応関数と市場価格のズレを見る補助材料。',
    },
    {
        'source_id': 'ois_public_proxy',
        'label': 'OIS / rates public proxy',
        'source_type': 'public_market_pricing_proxy',
        'source_scope': 'free_public_market_data_only',
        'summary': '金利市場が政策パスをどう織り込むかの補助材料。',
        'can_score_house_view': False,
        'usage': '市場織り込み差の参考。正解判定には使わない。',
    },
    {
        'source_id': 'economist_consensus_public',
        'label': 'Economist consensus public snippets',
        'source_type': 'public_consensus_snippet',
        'source_scope': 'free_public_snippets_only',
        'summary': '有料端末や会員サイトを使わず、公開範囲だけで確認できる市場予想。',
        'can_score_house_view': False,
        'usage': 'actual/consensus/surpriseの補助。欠損時は未取得として扱う。',
    },
]


def build_benchmark_outlook() -> dict:
    return {
        'source_scope': 'free_public_reference_benchmarks',
        'generated_at': timezone.now().isoformat(),
        'benchmark_outlooks': PUBLIC_BENCHMARKS,
        'audit': {
            'comparison_mode': 'reference_benchmarks_vs_live_house_view',
            'house_view_correctness_usage': 'not_allowed',
            'model_validation_usage': 'not_allowed',
            'paid_sources_allowed': False,
        },
        'limitations': [
            '有料API、有料端末、会員限定レポートは使わない。',
            '公開情報は更新頻度と粒度が不安定なため、モデル検証の代替にしない。',
        ],
    }
