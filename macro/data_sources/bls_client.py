"""BLS Public Data API 用の無料データソース境界。"""

BASE_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'


def source_metadata() -> dict:
    return {'source': 'BLS', 'base_url': BASE_URL, 'paid_api_required': False}
