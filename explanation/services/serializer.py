from zoneinfo import ZoneInfo


JST = ZoneInfo('Asia/Tokyo')


def snapshot_to_view(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    world_model = _world_model_from_basecalc(basecalc)
    scenario = snapshot.scenario or {}
    return {
        'snapshot': snapshot,
        'as_of_display': snapshot.as_of.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        'status_label': _status_label(snapshot.audit_level),
        'long_judgment': _trade_judgment('long', snapshot, world_model),
        'short_judgment': _trade_judgment('short', snapshot, world_model),
        'world_model_predictions': _world_model_predictions(world_model),
        'macro': {
            'bias': snapshot.macro_bias,
            'summary': macro.get('summary') or '',
        },
        'basecalc': {
            'bias': snapshot.basecalc_bias,
            'summary': basecalc.get('summary') or '',
            'resistance': (scenario.get('levels') or {}).get('resistance_display'),
            'support': (scenario.get('levels') or {}).get('support_display'),
            'invalidation': (scenario.get('levels') or {}).get('invalidation_display'),
        },
        'scenario': scenario,
        'reasons': list(snapshot.evidence or [])[:3],
        'audit_links': [
            {'label': 'Macro', 'url': '/macro/'},
            {'label': 'Basecalc', 'url': '/basecalc/'},
            {'label': 'Audit', 'url': '/explanation/audit/'},
        ],
    }


def snapshot_to_api(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    levels = (snapshot.scenario or {}).get('levels') or {}
    return {
        'as_of': snapshot.as_of.isoformat(),
        'final': {
            'label': snapshot.final_label,
            'stance': snapshot.final_stance,
            'action_posture': snapshot.action_posture,
            'confidence_score': snapshot.confidence_score,
            'confidence_grade': snapshot.confidence_grade,
            'status': _api_status(snapshot.audit_level),
        },
        'macro': {
            'bias': snapshot.macro_bias,
            'summary': macro.get('summary') or '',
        },
        'basecalc': {
            'bias': snapshot.basecalc_bias,
            'summary': basecalc.get('summary') or '',
            'resistance': levels.get('resistance'),
            'support': levels.get('support'),
            'invalidation': levels.get('invalidation'),
        },
        'audit': {
            'level': snapshot.audit_level,
            'items': snapshot.audit_items or [],
        },
    }


def _status_label(level):
    if level == 'blocked':
        return '判定保留。主要データに不足あり。'
    if level == 'warning':
        return '利用可。ただし一部データに警告あり。'
    return '利用可。'


def _api_status(level):
    if level == 'blocked':
        return 'blocked'
    if level == 'warning':
        return 'limited'
    return 'valid'


def _world_model_from_basecalc(basecalc):
    raw = basecalc.get('raw') or {}
    return raw.get('world_model') or (raw.get('data') or {}).get('world_model') or {}


def _trade_judgment(side, snapshot, world_model):
    target_key = 'upside_targets' if side == 'long' else 'downside_targets'
    target = _first_target(world_model.get(target_key))
    price = _format_price((target or {}).get('price'))
    probability = _format_probability(target or {})
    return {
        'label': 'ロング判断' if side == 'long' else 'ショート判断',
        'stance': _trade_stance(side, snapshot.final_stance),
        'price': f'{price}円' if price != 'N/A' else 'N/A',
        'probability': probability,
        'setup': world_model.get('primary_setup_label') or world_model.get('state_label') or '判断材料を確認中',
    }


def _trade_stance(side, final_stance):
    bullish = final_stance in {'bullish', 'conditional_bullish'}
    bearish = final_stance in {'bearish_alert', 'sell_rally_watch'}
    if side == 'long':
        if bullish:
            return '優先'
        if bearish:
            return '待機'
        return '様子見'
    if bearish:
        return '優先'
    if bullish:
        return '警戒のみ'
    return '様子見'


def _world_model_predictions(world_model):
    horizons = world_model.get('horizons') or {}
    return [
        {
            'horizon': horizon,
            'bias': _bias_label((horizons.get(horizon) or {}).get('main_bias')),
            'expected_return': _format_percent(
                (horizons.get(horizon) or {}).get('expected_return_pct')
                if (horizons.get(horizon) or {}).get('expected_return_pct') is not None
                else world_model.get(f'expected_return_{horizon}')
            ),
            'setup': (horizons.get(horizon) or {}).get('setup_label') or 'N/A',
        }
        for horizon in ('1d', '3d', '5d')
    ]


def _first_target(targets):
    for target in targets or []:
        if isinstance(target, dict) and target.get('price') is not None:
            return target
    return {}


def _format_price(value):
    try:
        return f'{float(value):,.0f}'
    except (TypeError, ValueError):
        return 'N/A'


def _format_percent(value):
    try:
        return f'{float(value):+.2f}%'
    except (TypeError, ValueError):
        return 'N/A'


def _format_probability(target):
    value = target.get('probability_display') or target.get('probability')
    if value is None:
        return '参考'
    text = str(value).strip()
    if text.endswith('%'):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if 0 <= number <= 1:
        return f'{number * 100:.0f}%'
    return f'{number:.0f}%'


def _bias_label(value):
    return {
        'up': '上',
        'down': '下',
        'range': '中立',
        'neutral': '中立',
    }.get(value, 'N/A')
