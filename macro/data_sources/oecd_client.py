"""OECD Data API 用の無料データソース境界。"""

BASE_URL = 'https://sdmx.oecd.org/public/rest'


def source_metadata() -> dict:
    return {'source': 'OECD', 'base_url': BASE_URL, 'paid_api_required': False}
