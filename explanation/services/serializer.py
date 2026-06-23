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
    trade_decision = _trade_decision(snapshot, world_model)
    return {
        'snapshot': snapshot,
        'as_of_display': snapshot.as_of.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        'status_label': manual_price.get('status_label') if manual_price.get('active') else _status_label(snapshot.audit_level),
        'confidence_display': _confidence_display(snapshot, manual_price),
        'manual_price': manual_price,
        'trade_decision': trade_decision,
        'decision_card': _decision_card(trade_decision, snapshot),
        'decision_inputs': _decision_inputs(snapshot, macro, basecalc, world_model, manual_price),
        'long_judgment': _trade_judgment('long', snapshot, world_model, trade_decision),
        'short_judgment': _trade_judgment('short', snapshot, world_model, trade_decision),
        'world_model_predictions': _world_model_predictions(world_model),
        'macro': {
            'bias': snapshot.macro_bias,
            'summary': macro.get('summary') or '',
        },
        'basecalc': {
            'bias': snapshot.basecalc_bias,
            'summary': _basecalc_summary(basecalc, world_model),
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
    trade_decision = _trade_decision(snapshot, _world_model_from_basecalc(basecalc))
    return {
        'as_of': snapshot.as_of.isoformat(),
        'version': snapshot.version,
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
        'trade_decision': trade_decision,
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


def _trade_judgment(side, snapshot, world_model, trade_decision=None):
    trade_decision = trade_decision or {}
    if trade_decision and trade_decision.get('decision_type') != 'legacy_reference':
        selected = trade_decision.get('selected_side')
        watch = trade_decision.get('reversal_watch') or {}
        target = trade_decision.get('target_1') if selected == side else None
        return {
            'label': 'ロング判断' if side == 'long' else 'ショート判断',
            'stance': _side_stance(side, trade_decision),
            'price': _target_display(target),
            'probability': _probability_display(trade_decision.get('probability') if selected == side else None),
            'setup': _side_setup(side, trade_decision, watch),
            'stop': _price_with_suffix(trade_decision.get('stop_price') if selected == side else None),
            'reward_risk': _rr_display(trade_decision.get('reward_risk') if selected == side else None),
            'reasons': _side_reasons(side, trade_decision),
        }
    output_contract = world_model.get('output_contract') or {}
    if world_model.get('contract_status') == 'error' or output_contract.get('contract_status') == 'error':
        reason = (world_model.get('stop_reasons') or output_contract.get('stop_reasons') or ['出力整合性を確認中'])[0]
        return {
            'label': 'ロング判断' if side == 'long' else 'ショート判断',
            'stance': '停止',
            'price': 'N/A',
            'probability': '表示停止',
            'setup': reason,
            'stop': 'N/A',
            'reward_risk': 'N/A',
            'reasons': [reason],
        }
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
        'stop': 'N/A',
        'reward_risk': 'N/A',
        'reasons': [],
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
    output_contract = world_model.get('output_contract') or {}
    allowed = output_contract.get('allowed_horizons') or {}
    return [
        {
            'horizon': horizon,
            'bias': '停止' if output_contract.get('contract_status') == 'error' or not (allowed.get(horizon) or {}).get('direction_allowed', True) else _bias_label((horizons.get(horizon) or {}).get('main_bias')),
            'expected_return': _format_percent(
                (horizons.get(horizon) or {}).get('expected_return_pct')
                if (horizons.get(horizon) or {}).get('expected_return_pct') is not None
                else world_model.get(f'expected_return_{horizon}')
            ),
            'setup': '方向判断停止' if output_contract.get('contract_status') == 'error' else (horizons.get(horizon) or {}).get('setup_label') or 'N/A',
        }
        for horizon in ('1d', '3d', '5d')
    ]


def _basecalc_summary(basecalc, world_model):
    output_contract = world_model.get('output_contract') or {}
    if world_model.get('contract_status') == 'error' or output_contract.get('contract_status') == 'error':
        reason = (world_model.get('stop_reasons') or output_contract.get('stop_reasons') or ['出力整合性を確認中'])[0]
        return f'basecalcの方向判断は停止。理由：{reason}'
    return basecalc.get('summary') or ''


def _first_target(targets):
    for target in targets or []:
        if isinstance(target, dict) and target.get('price') is not None:
            return target
    return {}


def _trade_decision(snapshot, world_model):
    decision = dict(snapshot.trade_decision or {})
    if decision:
        return decision
    return {
        'selected_side': _legacy_selected_side(snapshot.final_stance),
        'decision_type': 'legacy_reference',
        'horizon': '3d',
        'current_price': world_model.get('display_price') or world_model.get('price'),
        'entry_price': world_model.get('display_price') or world_model.get('price'),
        'target_1': None,
        'target_2': None,
        'stop_price': None,
        'invalidation_price': world_model.get('invalidation_price'),
        'reward_risk': None,
        'expected_return_pct': None,
        'probability': None,
        'confidence_score': snapshot.confidence_score,
        'confidence_grade': snapshot.confidence_grade,
        'long_score': 0,
        'short_score': 0,
        'no_trade_score': 0,
        'trend_follow_score': 0,
        'reversal_score': 0,
        'counter_scenario': world_model.get('counter_bias') or {},
        'reversal_watch': {},
        'reasons': list(snapshot.evidence or [])[:3],
        'warnings': [],
        'blocked_reasons': [],
        'model_version': snapshot.version,
        'price_source': 'market_data',
    }


def _legacy_selected_side(final_stance):
    if final_stance in {'bullish', 'conditional_bullish'}:
        return 'long'
    if final_stance in {'bearish_alert', 'sell_rally_watch'}:
        return 'short'
    return 'no_trade'


def _decision_card(trade_decision, snapshot):
    selected = trade_decision.get('selected_side') or 'no_trade'
    return {
        'label': _selected_side_label(selected),
        'decision_type': _decision_type_label(trade_decision.get('decision_type')),
        'current_price': _price_with_suffix(trade_decision.get('current_price')),
        'entry': _entry_display(trade_decision),
        'target': _target_display(trade_decision.get('target_1')),
        'stop': _price_with_suffix(trade_decision.get('stop_price')),
        'invalidation': _price_with_suffix(trade_decision.get('invalidation_price')),
        'reward_risk': _rr_display(trade_decision.get('reward_risk')),
        'confidence': _decision_confidence_display(trade_decision, snapshot),
        'counter': _counter_display(trade_decision),
        'reasons': list(trade_decision.get('reasons') or [])[:3],
        'warnings': list((trade_decision.get('warnings') or []) + (trade_decision.get('blocked_reasons') or []))[:4],
    }


def _decision_confidence_display(trade_decision, snapshot):
    grade = trade_decision.get('confidence_grade') or snapshot.confidence_grade
    score = trade_decision.get('confidence_score', snapshot.confidence_score)
    display = f'{grade} / {score}%'
    if _is_reference_decision(trade_decision):
        return f'参考判定（{display}）'
    return display


def _is_reference_decision(trade_decision):
    decision_type = trade_decision.get('decision_type') or ''
    return (
        trade_decision.get('selected_side') == 'no_trade'
        or decision_type.startswith('no_')
        or bool(trade_decision.get('blocked_reasons'))
    )


def _selected_side_label(value):
    return {
        'long': 'ロング',
        'short': 'ショート',
        'no_trade': '見送り',
    }.get(value, '見送り')


def _decision_type_label(value):
    return {
        'trend_follow': '順張り',
        'pullback': '押し目待ち',
        'rally_sell': '戻り売り',
        'reversal_watch': '逆張りWATCH',
        'reversal_entry': '逆張りENTRY',
        'no_chase_long': '高値追い禁止',
        'no_chase_short': '突っ込み売り禁止',
        'no_trade_conflict': '条件不足',
        'no_trade_data_blocked': 'データ停止',
        'legacy_reference': '参考判断',
    }.get(value, value or '条件確認')


def _entry_display(decision):
    is_reference = decision.get('selected_side') == 'no_trade'
    if is_reference and not decision.get('entry_price') and decision.get('entry_zone_low') is None:
        return 'なし'
    low = _format_price(decision.get('entry_zone_low'))
    high = _format_price(decision.get('entry_zone_high'))
    if low != 'N/A' and high != 'N/A':
        value = f'{low}〜{high}円'
    else:
        value = _price_with_suffix(decision.get('entry_price'))
    return f'参考 {value}' if is_reference and value != 'N/A' else value


def _counter_display(decision):
    watch = decision.get('reversal_watch') or {}
    if watch:
        side = _selected_side_label(watch.get('side'))
        status = 'ENTRY' if watch.get('status') == 'entry' else 'WATCH'
        return f"{side}逆張り{status}: {watch.get('label') or ''}".strip()
    counter = decision.get('counter_scenario') or {}
    return counter.get('label') or 'N/A'


def _side_stance(side, decision):
    selected = decision.get('selected_side')
    if selected == side:
        return '採用'
    watch = decision.get('reversal_watch') or {}
    if watch.get('side') == side:
        return 'WATCH' if watch.get('status') != 'entry' else 'ENTRY候補'
    if selected == 'no_trade':
        return '見送り'
    return '非採用'


def _side_setup(side, decision, watch):
    if decision.get('selected_side') == side:
        return _decision_type_label(decision.get('decision_type'))
    if watch.get('side') == side:
        return watch.get('label') or _decision_type_label('reversal_watch')
    reasons = decision.get('blocked_reasons') or []
    return reasons[0] if reasons else '採用条件なし'


def _side_reasons(side, decision):
    if decision.get('selected_side') == side:
        return list(decision.get('reasons') or [])[:3]
    watch = decision.get('reversal_watch') or {}
    if watch.get('side') == side:
        return list(watch.get('reasons') or [])[:3]
    return list(decision.get('blocked_reasons') or [])[:3]


def _target_display(target):
    if not target:
        return 'N/A'
    price = _format_price(target.get('price'))
    return f'{price}円' if price != 'N/A' else 'N/A'


def _price_with_suffix(value):
    price = _format_price(value)
    return f'{price}円' if price != 'N/A' else 'N/A'


def _rr_display(value):
    try:
        return f'{float(value):.2f}'
    except (TypeError, ValueError):
        return 'N/A'


def _probability_display(value):
    if value is None:
        return '参考'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 <= number <= 1:
        return f'{number * 100:.0f}%'
    return f'{number:.0f}%'


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
