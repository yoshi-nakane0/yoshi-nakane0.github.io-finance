from zoneinfo import ZoneInfo


JST = ZoneInfo('Asia/Tokyo')


def snapshot_to_view(snapshot):
    source = snapshot.source_snapshots or {}
    macro = source.get('macro') or {}
    basecalc = source.get('basecalc') or {}
    scenario = snapshot.scenario or {}
    return {
        'snapshot': snapshot,
        'as_of_display': snapshot.as_of.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        'status_label': _status_label(snapshot.audit_level),
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
