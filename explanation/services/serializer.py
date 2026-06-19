from datetime import datetime
from zoneinfo import ZoneInfo


JST = ZoneInfo('Asia/Tokyo')


def snapshot_to_view(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    world_model = _world_model_from_basecalc(basecalc)
    scenario = snapshot.scenario or {}
    manual_price = _manual_price_from_basecalc(basecalc)
    return {
        'snapshot': snapshot,
        'as_of_display': snapshot.as_of.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        'status_label': manual_price.get('status_label') if manual_price.get('active') else _status_label(snapshot.audit_level),
        'confidence_display': _confidence_display(snapshot, manual_price),
        'manual_price': manual_price,
        'decision_inputs': _decision_inputs(snapshot, macro, basecalc, world_model, manual_price),
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


def _manual_price_from_basecalc(basecalc):
    raw = basecalc.get('raw') or {}
    manual = raw.get('manual_price_override') or {}
    if not manual.get('active'):
        return {
            'active': False,
            'price': None,
            'price_display': '',
            'status_label': '',
            'summary': '',
            'source_rows': [],
        }
    price = manual.get('price')
    price_display = manual.get('price_display') or _format_price(price)
    mode = raw.get('manual_price_mode') or {}
    return {
        'active': True,
        'price': price,
        'price_display': price_display,
        'status_label': '手入力価格による一時総合判定。',
        'summary': f'{price_display}円を現在値として、MacroとBasecalcを総合しています。',
        'source_rows': [
            {'label': '判定対象価格', 'value': f'{price_display}円（手入力）'},
            {'label': 'Macro', 'value': mode.get('macro_source') or '保存済み最新判断'},
            {'label': 'Basecalc', 'value': mode.get('basecalc_source') or '保存済みチャート判断に手入力価格を反映'},
        ],
    }


def _decision_inputs(snapshot, macro, basecalc, world_model, manual_price):
    macro_raw = macro.get('raw') or {}
    basecalc_raw = basecalc.get('raw') or {}
    return {
        'rows': [
            {
                'label': 'Macroデータ更新時刻',
                'value': _format_datetime(macro_raw.get('generated_at') or macro.get('as_of')),
            },
            {
                'label': 'Basecalcデータ更新時刻',
                'value': _basecalc_updated_display(basecalc_raw, world_model),
            },
            {
                'label': '手入力価格',
                'value': f"{manual_price.get('price_display')}円" if manual_price.get('active') else '未入力',
            },
            {
                'label': '米国3指数',
                'value': _us_index_availability(basecalc_raw, world_model),
            },
        ],
        'materials': list(snapshot.evidence or [])[:6],
    }


def _basecalc_updated_display(basecalc_raw, world_model):
    value = (
        basecalc_raw.get('generated_at')
        or world_model.get('generated_at')
        or world_model.get('as_of')
    )
    formatted = _format_datetime(value)
    if formatted != 'N/A':
        return formatted
    display = world_model.get('last_updated_display')
    return display or 'N/A'


def _us_index_availability(basecalc_raw, world_model):
    intermarket = (
        world_model.get('us_index_confirmation')
        or world_model.get('intermarket_technicals')
        or basecalc_raw.get('intermarket_technicals')
        or {}
    )
    readiness = intermarket.get('readiness') if isinstance(intermarket, dict) else {}
    components = intermarket.get('components') if isinstance(intermarket, dict) else {}
    readiness = readiness if isinstance(readiness, dict) else {}
    components = components if isinstance(components, dict) else {}
    if readiness.get('usable') is False:
        return 'なし'
    return 'あり' if components else 'なし'


def _format_datetime(value):
    if not value:
        return 'N/A'
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(' JST'):
            return text
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return text or 'N/A'
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST).strftime('%Y-%m-%d %H:%M JST')


def _confidence_display(snapshot, manual_price):
    if manual_price.get('active'):
        return '参考判定（価格は手入力）'
    return f'{snapshot.confidence_grade} / {snapshot.confidence_score}%'


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
