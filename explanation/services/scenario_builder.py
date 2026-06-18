def build_scenarios(macro, basecalc):
    resistance = _price_display(basecalc.resistance)
    support = _price_display(basecalc.support)
    invalidation = _price_display(basecalc.invalidation)

    return {
        'baseline': {
            'title': '基本シナリオ',
            'text': '上昇基調は維持。ただし高値追いではなく押し目確認を優先。',
        },
        'upside': {
            'title': '上振れシナリオ',
            'text': f'{resistance}円を明確に上抜き、米国3指数も改善すれば上値拡張。',
        },
        'downside': {
            'title': '下振れシナリオ',
            'text': f'{support}円を割り込み、米国3指数も失速すれば上昇失敗。',
        },
        'levels': {
            'resistance': basecalc.resistance,
            'support': basecalc.support,
            'invalidation': basecalc.invalidation,
            'resistance_display': resistance,
            'support_display': support,
            'invalidation_display': invalidation,
        },
        'change_condition': (
            f'{support}円割れで上昇シナリオを弱める。'
            f'{resistance}円突破と米国3指数改善で上値拡張を見る。'
            f'無効化ラインは{invalidation}円。'
        ),
    }


def _price_display(value):
    if value is None:
        return 'N/A'
    return f'{float(value):,.0f}'
