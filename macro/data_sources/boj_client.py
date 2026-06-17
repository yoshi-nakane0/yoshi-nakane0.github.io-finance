"""BOJ 時系列統計データ検索API用の境界。"""

BASE_URL = 'https://api.stat-search.boj.or.jp'


def source_metadata() -> dict:
    return {'source': 'BOJ', 'base_url': BASE_URL, 'paid_api_required': False}
