"""BEA API 用の無料データソース境界。"""

BASE_URL = 'https://apps.bea.gov/api/data'


def source_metadata() -> dict:
    return {'source': 'BEA', 'base_url': BASE_URL, 'paid_api_required': False}
