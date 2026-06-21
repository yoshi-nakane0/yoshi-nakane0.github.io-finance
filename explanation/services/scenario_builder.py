def build_scenarios(macro, basecalc):
    if getattr(basecalc, 'contract_status', 'unchecked') == 'error':
        reason = (getattr(basecalc, 'stop_reasons', None) or ['basecalcの出力整合性を確認中'])[0]
        return {
            'baseline': {
                'title': '基本シナリオ',
                'text': f'basecalcの方向判断は停止。理由：{reason}',
            },
            'upside': {
                'title': '上振れシナリオ',
                'text': '上値拡張は再計算と米国3指数確認がそろうまで表示停止。',
            },
            'downside': {
                'title': '下振れシナリオ',
                'text': '下値確認は支持抵抗とATRレンジのみ参考。',
            },
            'levels': {
                'resistance': None,
                'support': None,
                'invalidation': None,
                'resistance_display': 'N/A',
                'support_display': 'N/A',
                'invalidation_display': 'N/A',
            },
            'change_condition': '再計算後、価格・ターゲット・レンジの時点が一致した場合のみ再開。',
        }
    resistance = _price_display(basecalc.resistance)
    support = _price_display(basecalc.support)
    invalidation = _price_display(basecalc.invalidation)
    upside_text = (
        '上値拡張は米国3指数確認まで表示停止。'
        if getattr(basecalc, 'us_index_available', True) is False
        else f'{resistance}円を明確に上抜き、米国3指数も改善すれば上値拡張。'
    )

    return {
        'baseline': {
            'title': '基本シナリオ',
            'text': '上昇基調は維持。ただし高値追いではなく押し目確認を優先。',
        },
        'upside': {
            'title': '上振れシナリオ',
            'text': upside_text,
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
