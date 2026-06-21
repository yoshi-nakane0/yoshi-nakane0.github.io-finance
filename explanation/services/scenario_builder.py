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
    downside_text = f'{support}円を割り込み、米国3指数も失速すれば下落継続。'
    baseline_text, change_condition = _directional_baseline(macro, basecalc, support, resistance, invalidation)

    return {
        'baseline': {
            'title': '基本シナリオ',
            'text': baseline_text,
        },
        'upside': {
            'title': '上振れシナリオ',
            'text': upside_text,
        },
        'downside': {
            'title': '下振れシナリオ',
            'text': downside_text,
        },
        'levels': {
            'resistance': basecalc.resistance,
            'support': basecalc.support,
            'invalidation': basecalc.invalidation,
            'resistance_display': resistance,
            'support_display': support,
            'invalidation_display': invalidation,
        },
        'change_condition': change_condition,
    }


def _price_display(value):
    if value is None:
        return 'N/A'
    return f'{float(value):,.0f}'


def _directional_baseline(macro, basecalc, support, resistance, invalidation):
    macro_bias = getattr(macro, 'bias', 'neutral')
    reversal_risk = int(getattr(basecalc, 'reversal_risk_score', 0) or 0)
    rebound_risk = int(getattr(basecalc, 'rebound_improvement_score', 0) or 0)
    if getattr(basecalc, 'bias', '') == 'bullish':
        if reversal_risk >= 70 or macro_bias in {'negative', 'neutral_inflation_risk'}:
            return (
                '上昇方向は残るが、高値追いは禁止。反落WATCHとして利確優先。',
                f'{support}円割れで上昇判定を撤回。{resistance}円突破でも過熱が残る場合は追撃しない。無効化ラインは{invalidation}円。',
            )
        return (
            '上昇継続を基本にするが、押し目確認とR/R成立を条件にする。',
            f'{support}円割れで上昇シナリオを弱める。{resistance}円突破と米国3指数改善で上値拡張を見る。無効化ラインは{invalidation}円。',
        )
    if getattr(basecalc, 'bias', '') == 'bearish':
        if rebound_risk >= 70 or macro_bias == 'positive':
            return (
                '下落方向は残るが、突っ込み売りは禁止。買い戻しWATCHとして戻りを確認する。',
                f'{resistance}円超えで下落判定を撤回。{support}円割れでも売られすぎが残る場合は追撃しない。無効化ラインは{invalidation}円。',
            )
        return (
            '下落継続を基本にし、戻り売り条件とR/R成立を確認する。',
            f'{resistance}円超えで下落シナリオを弱める。{support}円割れと米国3指数失速で下値継続を見る。無効化ラインは{invalidation}円。',
        )
    return (
        'レンジ内は見送り。方向、target、stop、R/Rがそろうまで待つ。',
        f'{resistance}円突破なら上方向、{support}円割れなら下方向を再評価。無効化ラインは{invalidation}円。',
    )
