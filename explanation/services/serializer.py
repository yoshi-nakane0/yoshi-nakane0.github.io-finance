from copy import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from .beginner_decision import build_beginner_decision
from .readiness_score import build_readiness_score


JST = ZoneInfo('Asia/Tokyo')


def snapshot_to_view(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    world_model = _world_model_from_basecalc(basecalc)
    scenario = snapshot.scenario or {}
    manual_price = _manual_price_from_basecalc(basecalc)
    trade_decision = _trade_decision(snapshot, world_model)
    beginner_decision = build_beginner_decision(
        snapshot,
        macro,
        basecalc,
        world_model,
        trade_decision,
        manual_price,
    )
    decision_card = _decision_card(trade_decision, snapshot)
    long_judgment = _trade_judgment('long', snapshot, world_model, trade_decision)
    short_judgment = _trade_judgment('short', snapshot, world_model, trade_decision)
    world_model_predictions = _world_model_predictions(world_model, manual_price, trade_decision)
    world_model_gate = _world_model_gate_summary(world_model, trade_decision, snapshot)
    alignment_summary = _alignment_summary(snapshot)
    adoption_summary = _adoption_summary(
        decision_card,
        long_judgment,
        short_judgment,
        trade_decision,
        snapshot,
    )
    return {
        'snapshot': snapshot,
        'as_of_display': snapshot.as_of.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        'status_label': manual_price.get('status_label') if manual_price.get('active') else _status_label(snapshot.audit_level),
        'confidence_display': _confidence_display(snapshot, manual_price),
        'manual_price': manual_price,
        'trade_decision': trade_decision,
        'beginner_decision': beginner_decision,
        'next_action': beginner_decision.get('next_triggers') or {},
        'decision_card': decision_card,
        'integrated_decision': _integrated_decision(snapshot, decision_card, trade_decision, manual_price),
        'alignment_summary': alignment_summary,
        'adoption_summary': adoption_summary,
        'validation_summary': _validation_summary_placeholder(snapshot),
        'advanced_detail': {
            'legacy_final': {
                'label': snapshot.final_label,
                'stance': snapshot.final_stance,
                'action_posture': snapshot.action_posture,
                'confidence': _confidence_display(snapshot, manual_price),
                'status': _status_label(snapshot.audit_level),
            },
            'long_judgment': long_judgment,
            'short_judgment': short_judgment,
            'world_model_predictions': world_model_predictions,
            'world_model_gate': world_model_gate,
            'decision_inputs': _decision_inputs(snapshot, macro, basecalc, world_model, manual_price),
            'audit_items': snapshot.audit_items or [],
        },
        'decision_inputs': _decision_inputs(snapshot, macro, basecalc, world_model, manual_price),
        'long_judgment': long_judgment,
        'short_judgment': short_judgment,
        'world_model_predictions': world_model_predictions,
        'world_model_gate': world_model_gate,
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
        'reasons': _normalized_list(snapshot.evidence or [])[:3],
        'audit_links': [
            {'label': 'Macro', 'url': '/macro/'},
            {'label': 'Basecalc', 'url': '/basecalc/'},
            {'label': 'Audit', 'url': '/explanation/audit/'},
        ],
    }


def _integrated_decision(snapshot, decision_card, trade_decision, manual_price):
    posture = _integrated_posture(decision_card.get('label'), snapshot.final_stance)
    return {
        'posture': posture,
        'current_price': decision_card.get('current_price') or 'N/A',
        'entry': decision_card.get('entry') or '判定不可',
        'target': decision_card.get('target') or 'N/A',
        'stop_or_invalidation': (
            decision_card.get('stop')
            if decision_card.get('stop') != 'N/A'
            else decision_card.get('invalidation')
        ) or 'N/A',
        'reward_risk': decision_card.get('reward_risk') or 'N/A',
        'confidence': decision_card.get('confidence') or _confidence_display(snapshot, manual_price),
        'judgment_state': _integrated_status(snapshot, trade_decision, manual_price),
        'counter': decision_card.get('counter') or 'N/A',
    }


def _integrated_posture(label, final_stance):
    if label == 'ロング':
        return 'ロング候補'
    if label == 'ショート':
        return 'ショート候補'
    if final_stance in {'neutral_wait', 'withhold'}:
        return '待機'
    return '見送り'


def _integrated_status(snapshot, trade_decision, manual_price):
    status = trade_decision.get('decision_status')
    if snapshot.audit_level == 'blocked' or trade_decision.get('decision_type') == 'no_trade_data_blocked':
        return '判定停止'
    if status == 'blocked':
        return '判定停止'
    if status == 'watch_only':
        return '監視'
    if status == 'candidate_limited':
        return '限定'
    if status == 'candidate_confirmed':
        return '通常'
    if manual_price.get('active') or _is_reference_decision(trade_decision):
        return '参考'
    if status == 'wait':
        return '見送り'
    return '判定可'


def _alignment_summary(snapshot):
    macro_label = _macro_impact_label(snapshot.macro_bias)
    basecalc_label = _basecalc_state_label(snapshot.basecalc_bias)
    return {
        'macro': macro_label,
        'basecalc': basecalc_label,
        'status': _alignment_label(snapshot.alignment_status, macro_label, basecalc_label),
        'action': _alignment_action(snapshot.alignment_status, macro_label, basecalc_label),
    }


def _macro_impact_label(value):
    if value == 'positive':
        return '追い風'
    if value == 'negative':
        return '逆風'
    return '中立'


def _basecalc_state_label(value):
    if value == 'bullish':
        return '上方向'
    if value == 'bearish':
        return '下方向'
    if value in {'range', 'neutral'}:
        return 'レンジ'
    return '中立'


def _alignment_label(alignment_status, macro_label, basecalc_label):
    if alignment_status == 'blocked':
        return '判定停止'
    if macro_label == '中立' and basecalc_label in {'中立', 'レンジ'}:
        return '方向なしで一致'
    if macro_label == '中立' or basecalc_label in {'中立', 'レンジ'}:
        return '片側中立'
    if alignment_status == 'aligned':
        return '同方向'
    if alignment_status == 'timeframe_divergence':
        return '時間軸分岐'
    return '片側中立'


def _alignment_action(alignment_status, macro_label, basecalc_label):
    if alignment_status == 'blocked':
        return '鮮度不足または主要データ不足のため、最終判断を止める。'
    if macro_label == '中立' and basecalc_label in {'中立', 'レンジ'}:
        return 'MacroもBasecalcも方向を出していないため、順張り候補ではなく待機を基本にする。'
    if macro_label == '中立' or basecalc_label in {'中立', 'レンジ'}:
        return '片方のみ方向あり。方向がはっきりするまで条件待ち。'
    if alignment_status == 'aligned':
        return 'MacroとBasecalcの方向が一致。ゲート通過時のみ順張り候補。'
    if alignment_status == 'timeframe_divergence':
        return '短期と中期を分けて扱い、追撃を避ける。'
    return '片方のみ方向あり。方向がはっきりするまで条件待ち。'


def _adoption_summary(decision_card, long_judgment, short_judgment, trade_decision, snapshot):
    reasons = list(decision_card.get('reasons') or snapshot.evidence or [])[:3]
    warnings = list(decision_card.get('warnings') or trade_decision.get('warnings') or [])[:3]
    if not warnings:
        warnings = ['反対シナリオと無効化ラインを確認。']
    return {
        'primary': _integrated_posture(decision_card.get('label'), snapshot.final_stance),
        'long_condition': f"Long: {long_judgment.get('stance')} / {long_judgment.get('setup')}",
        'short_condition': f"Short: {short_judgment.get('stance')} / {short_judgment.get('setup')}",
        'reasons': reasons,
        'warnings': warnings,
    }


def _validation_summary_placeholder(snapshot):
    if snapshot.confidence_score >= 70:
        state = '十分'
    elif snapshot.confidence_score >= 50:
        state = '少ない'
    else:
        state = '未検証'
    return {
        'one_line': f"検証状態: {state} / 信頼度 {snapshot.confidence_grade} {snapshot.confidence_score}%",
    }


def snapshot_to_api(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    levels = (snapshot.scenario or {}).get('levels') or {}
    trade_decision = _trade_decision(snapshot, _world_model_from_basecalc(basecalc))
    score_bundle = (snapshot.score_breakdown or {}).get('score_bundle')
    if not score_bundle:
        score_bundle = build_readiness_score(_snapshot_with_trade_decision(snapshot, trade_decision), {'available': False})
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
        'score_bundle': score_bundle,
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
    static_metadata = getattr(snapshot, 'static_metadata', {}) or {}
    return {
        'rows': [
            {
                'label': 'Explanation作成時刻',
                'value': _format_datetime(snapshot.as_of),
            },
            {
                'label': 'Macro判定作成時刻',
                'value': _format_datetime(macro_raw.get('generated_at') or macro.get('as_of')),
            },
            {
                'label': 'Basecalc判定作成時刻',
                'value': _basecalc_updated_display(basecalc_raw, world_model),
            },
            {
                'label': 'Basecalc市場価格取得時刻',
                'value': _format_datetime(
                    basecalc_raw.get('fetched_at')
                    or world_model.get('fetched_at')
                    or ((world_model.get('features') or {}).get('fetched_at'))
                ),
            },
            {
                'label': '表示価格',
                'value': _price_with_suffix(
                    world_model.get('display_price')
                    or world_model.get('price')
                    or (snapshot.trade_decision or {}).get('current_price')
                ),
            },
            {
                'label': '価格ソース',
                'value': (snapshot.trade_decision or {}).get('price_source') or 'saved_snapshot',
            },
            {
                'label': '手入力価格',
                'value': f"{manual_price.get('price_display')}円" if manual_price.get('active') else '未入力',
            },
            {
                'label': '米国3指数',
                'value': _us_index_availability(basecalc_raw, world_model),
            },
            {
                'label': 'Snapshot Key',
                'value': static_metadata.get('snapshot_key') or 'N/A',
            },
            {
                'label': 'Git SHA',
                'value': _short_sha(static_metadata.get('git_sha')),
            },
            {
                'label': 'Workflow Run ID',
                'value': static_metadata.get('workflow_run_id') or 'N/A',
            },
        ],
        'materials': _normalized_list(snapshot.evidence or [])[:6],
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


def _short_sha(value):
    value = str(value or '').strip()
    return value[:12] if value else 'N/A'


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
        reason = (_basecalc_stop_reasons(world_model, output_contract) or ['出力整合性を確認中'])[0]
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


def _world_model_predictions(world_model, manual_price=None, trade_decision=None):
    horizons = world_model.get('horizons') or {}
    output_contract = world_model.get('output_contract') or {}
    allowed = output_contract.get('allowed_horizons') or {}
    trade_decision = trade_decision or {}
    gate_blocked = _direction_gate_blocked(world_model, trade_decision)
    base_price = _prediction_base_price(world_model, manual_price or {}, trade_decision or {})
    rows = []
    for horizon in ('1d', '3d', '5d'):
        stopped = gate_blocked or _world_model_horizon_stopped(world_model, output_contract, allowed, horizon)
        expected_return = _expected_return_pct(world_model, horizons, horizon)
        rows.append({
            'horizon': horizon,
            'bias': '停止 / 参考' if stopped else _bias_label((horizons.get(horizon) or {}).get('main_bias')),
            'expected_return': 'N/A' if stopped else _format_percent(expected_return),
            'expected_price': 'N/A' if stopped else _expected_price_display(base_price, expected_return),
            'base_price': _price_with_suffix(base_price),
            'setup': '方向ゲート停止中（売買判定には未使用）' if stopped else (horizons.get(horizon) or {}).get('setup_label') or 'N/A',
        })
    return rows


def _world_model_gate_summary(world_model, trade_decision, snapshot):
    trade_decision = trade_decision or {}
    output_contract = world_model.get('output_contract') or {}
    allowed = output_contract.get('allowed_horizons') or {}
    gate_blocked = _direction_gate_blocked(world_model, trade_decision)
    horizon_stopped = any(
        _world_model_horizon_stopped(world_model, output_contract, allowed, horizon)
        for horizon in ('1d', '3d', '5d')
    )
    if not gate_blocked and not horizon_stopped:
        return {'available': False, 'stop_reason': '', 'restart_condition': ''}
    reasons = _dedupe_nonempty(
        _normalized_list(trade_decision.get('blocked_reasons') or [])
        + _basecalc_stop_reasons(world_model, output_contract)
        + _blocking_audit_items(getattr(snapshot, 'audit_items', []) or [])
    )
    if not reasons:
        reasons = ['方向ゲート停止中']
    conditions = _restart_conditions_for_reasons(reasons)
    return {
        'available': True,
        'stop_reason': ' / '.join(reasons[:4]),
        'restart_condition': ' / '.join(conditions),
    }


def _restart_conditions_for_reasons(reasons):
    conditions = []
    joined = ' '.join(reasons)
    if '方向' in joined or '予測ゲート' in joined:
        conditions.append('方向ゲート再開')
    if '米国3指数' in joined:
        conditions.append('米国3指数確認')
    if '信頼度' in joined:
        conditions.append('信頼度回復')
    if '鮮度' in joined or 'データ' in joined or '不一致' in joined or '現在値' in joined or '価格' in joined:
        conditions.append('データ不足解消')
    if '重要指標' in joined:
        conditions.append('重要指標通過')
    return _dedupe_nonempty(conditions) or ['方向ゲート再開', 'データ不足解消', '信頼度回復']


def _blocking_audit_items(items):
    return [
        item for item in _normalized_list(items)
        if item != '監査では判断を止める問題は確認されていない。'
    ]


def _dedupe_nonempty(items):
    result = []
    for item in items or []:
        value = str(item or '').strip()
        if value and value not in result:
            result.append(value)
    return result


def _direction_gate_blocked(world_model, trade_decision):
    output_contract = world_model.get('output_contract') or {}
    decision_type = trade_decision.get('decision_type') or ''
    if output_contract.get('contract_status') == 'error' or world_model.get('contract_status') == 'error':
        return True
    if output_contract.get('directional_allowed') is False:
        return True
    if trade_decision.get('selected_side') == 'no_trade' and decision_type.startswith('no_'):
        return True
    if trade_decision.get('blocked_reasons'):
        return True
    if world_model.get('can_show_prediction') is False:
        return True
    if world_model.get('allowed_direction') in {'stopped', 'none'}:
        return True
    return False


def _world_model_horizon_stopped(world_model, output_contract, allowed, horizon):
    if world_model.get('contract_status') == 'error' or output_contract.get('contract_status') == 'error':
        return True
    horizon_gate = allowed.get(horizon)
    if isinstance(horizon_gate, dict) and horizon_gate.get('direction_allowed') is False:
        return True
    if output_contract.get('directional_allowed') is False:
        return True
    if world_model.get('can_show_prediction') is False:
        return True
    if world_model.get('allowed_direction') in {'stopped', 'none'}:
        return True
    return False


def _expected_return_pct(world_model, horizons, horizon):
    horizon_data = horizons.get(horizon) or {}
    if horizon_data.get('expected_return_pct') is not None:
        return horizon_data.get('expected_return_pct')
    return world_model.get(f'expected_return_{horizon}')


def _prediction_base_price(world_model, manual_price, trade_decision):
    if manual_price.get('active'):
        return manual_price.get('price')
    return (
        world_model.get('display_price')
        or world_model.get('price')
        or trade_decision.get('current_price')
    )


def _expected_price_display(base_price, expected_return_pct):
    price = _number_or_none(base_price)
    expected_return = _number_or_none(expected_return_pct)
    if price is None or expected_return is None:
        return 'N/A'
    return _price_with_suffix(price * (1 + expected_return / 100))


def _basecalc_summary(basecalc, world_model):
    output_contract = world_model.get('output_contract') or {}
    if world_model.get('contract_status') == 'error' or output_contract.get('contract_status') == 'error':
        reason = (_basecalc_stop_reasons(world_model, output_contract) or ['出力整合性を確認中'])[0]
        return f'basecalcの方向判断は停止。理由：{reason}'
    return basecalc.get('summary') or ''


def _basecalc_stop_reasons(world_model, output_contract):
    return _dedupe_nonempty(
        _normalized_list(world_model.get('hard_stop_reasons') or [])
        + _normalized_list(output_contract.get('hard_stop_reasons') or [])
        + _normalized_list(world_model.get('hard_block_reasons') or [])
        + _normalized_list(output_contract.get('hard_block_reasons') or [])
        + _normalized_list(world_model.get('stop_reasons') or [])
        + _normalized_list(output_contract.get('stop_reasons') or [])
    )


def _first_target(targets):
    for target in targets or []:
        if isinstance(target, dict) and target.get('price') is not None:
            return target
    return {}


def _trade_decision(snapshot, world_model):
    decision = dict(snapshot.trade_decision or {})
    defaults = {
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
        'expected_value': None,
        'confidence_score': snapshot.confidence_score,
        'confidence_grade': snapshot.confidence_grade,
        'long_score': 0,
        'short_score': 0,
        'no_trade_score': 0,
        'trend_follow_score': 0,
        'reversal_score': 0,
        'counter_scenario': world_model.get('counter_bias') or {},
        'reversal_watch': {},
        'reasons': _normalized_list(snapshot.evidence or [])[:3],
        'warnings': [],
        'blocked_reasons': [],
        'model_version': snapshot.version,
        'price_source': 'market_data',
        'decision_status': 'wait',
        'entry_permission': 'no_entry',
        'validation_level': 'none',
        'hard_block_reasons': [],
        'soft_warning_reasons': [],
        'confidence_components': {},
        'position_size_pct': 0,
        'position_size_cap': 'none',
    }
    if decision:
        return {**defaults, **decision}
    return defaults


def _snapshot_with_trade_decision(snapshot, trade_decision):
    normalized = copy(snapshot)
    normalized.trade_decision = trade_decision
    return normalized


def _legacy_selected_side(final_stance):
    if final_stance in {'bullish', 'conditional_bullish'}:
        return 'long'
    if final_stance in {'bearish_alert', 'sell_rally_watch'}:
        return 'short'
    return 'no_trade'


def _decision_card(trade_decision, snapshot):
    selected = trade_decision.get('selected_side') or 'no_trade'
    is_reference = _is_reference_decision(trade_decision)
    return {
        'label': _selected_side_label(selected),
        'decision_type': _decision_type_label(trade_decision.get('decision_type')),
        'current_price': _price_with_suffix(trade_decision.get('current_price')),
        'entry': _entry_display(trade_decision),
        'target': 'N/A' if is_reference else _target_display(trade_decision.get('target_1')),
        'stop': 'N/A' if is_reference else _price_with_suffix(trade_decision.get('stop_price')),
        'invalidation': 'N/A' if is_reference else _price_with_suffix(trade_decision.get('invalidation_price')),
        'reward_risk': _blocked_rr_display(trade_decision) if is_reference else _rr_display(trade_decision.get('reward_risk')),
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
        or decision_type == 'legacy_reference'
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
        'no_trade_direction_stopped': '方向予測停止',
        'legacy_reference': '参考判断',
    }.get(value, value or '条件確認')


def _entry_display(decision):
    is_reference = _is_reference_decision(decision)
    if is_reference:
        return 'なし'
    low = _format_price(decision.get('entry_zone_low'))
    high = _format_price(decision.get('entry_zone_high'))
    if low != 'N/A' and high != 'N/A':
        value = f'{low}〜{high}円'
    else:
        value = _price_with_suffix(decision.get('entry_price'))
    return value


def _blocked_rr_display(decision):
    value = decision.get('reward_risk')
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '不採用'
    if number < 1.2:
        return '不採用（1.2未満）'
    return '不採用'


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
    number = _number_or_none(value)
    if number is None:
        return 'N/A'
    return f'{number:,.0f}'


def _format_percent(value):
    try:
        return f'{float(value):+.2f}%'
    except (TypeError, ValueError):
        return 'N/A'


def _number_or_none(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(',', '').replace('円', '').replace('%', '').strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _normalized_list(items):
    return [_normalize_reason_text(item) for item in items if item]


def _normalize_reason_text(text):
    text = str(text or '').strip()
    text = text.replace('重要指標の発表前後のため一段階下げます。のため、強い判断にはしない。', '重要指標の発表前後のため、強い判断にはしない。')
    text = text.replace('ます。のため', 'ます。そのため')
    text = text.replace('。のため', '。そのため')
    text = text.replace('ため一段階下げます。そのため、強い判断にはしない。', '重要指標の発表前後のため、強い判断にはしない。')
    return text


def _bias_label(value):
    return {
        'up': '上',
        'down': '下',
        'range': '中立',
        'neutral': '中立',
    }.get(value, 'N/A')
