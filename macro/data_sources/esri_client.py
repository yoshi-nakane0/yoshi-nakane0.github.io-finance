"""内閣府ESRI公表データ用の境界。"""

BASE_URL = 'https://www.esri.cao.go.jp'


def source_metadata() -> dict:
    return {'source': 'ESRI', 'base_url': BASE_URL, 'paid_api_required': False}
