# control/views.py
from django.shortcuts import render
import json

def get_fed_monitor_data():
    """Fed Rate Monitor Tool データ"""
    return {
        '2025-07-30': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '2.1%', 'oneDay': '2.3%', 'oneWeek': '3.1%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '8.7%', 'oneDay': '9.2%', 'oneWeek': '11.4%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '15.3%', 'oneDay': '16.1%', 'oneWeek': '18.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '22.8%', 'oneDay': '23.4%', 'oneWeek': '25.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '35.4%', 'oneDay': '34.8%', 'oneWeek': '32.7%', 'type': 'positive'},
                {'range': '4.25-4.50%', 'current': '15.7%', 'oneDay': '14.2%', 'oneWeek': '8.8%', 'type': 'negative'}
            ]
        },
        '2025-09-17': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '5.2%', 'oneDay': '5.8%', 'oneWeek': '7.3%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '12.4%', 'oneDay': '13.1%', 'oneWeek': '15.7%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '25.8%', 'oneDay': '26.3%', 'oneWeek': '28.1%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '31.2%', 'oneDay': '30.8%', 'oneWeek': '29.4%', 'type': 'positive'},
                {'range': '4.00-4.25%', 'current': '20.1%', 'oneDay': '19.7%', 'oneWeek': '16.9%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '5.3%', 'oneDay': '4.3%', 'oneWeek': '2.6%', 'type': 'negative'}
            ]
        },
        '2025-10-29': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '8.9%', 'oneDay': '9.4%', 'oneWeek': '11.2%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '18.7%', 'oneDay': '19.3%', 'oneWeek': '21.5%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '34.1%', 'oneDay': '33.8%', 'oneWeek': '32.9%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '25.8%', 'oneDay': '25.2%', 'oneWeek': '23.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '10.9%', 'oneDay': '10.1%', 'oneWeek': '9.2%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '1.6%', 'oneDay': '2.2%', 'oneWeek': '2.1%', 'type': 'negative'}
            ]
        },
        '2025-12-10': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '12.5%', 'oneDay': '13.1%', 'oneWeek': '15.4%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '28.3%', 'oneDay': '28.9%', 'oneWeek': '30.2%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '35.7%', 'oneDay': '35.1%', 'oneWeek': '33.8%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '18.2%', 'oneDay': '17.8%', 'oneWeek': '16.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '4.8%', 'oneDay': '4.6%', 'oneWeek': '4.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.5%', 'oneDay': '0.5%', 'oneWeek': '0.4%', 'type': 'negative'}
            ]
        },
        '2026-01-26': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '18.4%', 'oneDay': '19.1%', 'oneWeek': '21.2%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '42.1%', 'oneDay': '41.8%', 'oneWeek': '40.3%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '28.7%', 'oneDay': '28.2%', 'oneWeek': '26.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '9.3%', 'oneDay': '9.1%', 'oneWeek': '10.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '1.3%', 'oneDay': '1.6%', 'oneWeek': '1.3%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.2%', 'oneDay': '0.2%', 'oneWeek': '0.2%', 'type': 'negative'}
            ]
        },
        '2026-03-18': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '28.9%', 'oneDay': '29.7%', 'oneWeek': '31.2%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '51.2%', 'oneDay': '50.1%', 'oneWeek': '47.8%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '17.1%', 'oneDay': '17.5%', 'oneWeek': '18.3%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '2.5%', 'oneDay': '2.4%', 'oneWeek': '2.4%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.3%', 'oneDay': '0.3%', 'oneWeek': '0.3%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'}
            ]
        },
        '2026-04-29': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '35.8%', 'oneDay': '36.4%', 'oneWeek': '38.1%', 'type': 'positive'},
                {'range': '3.25-3.50%', 'current': '43.7%', 'oneDay': '42.9%', 'oneWeek': '40.2%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '18.2%', 'oneDay': '18.5%', 'oneWeek': '19.4%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '2.1%', 'oneDay': '2.0%', 'oneWeek': '2.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.2%', 'oneDay': '0.2%', 'oneWeek': '0.2%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'}
            ]
        },
        '2026-06-17': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '48.3%', 'oneDay': '47.9%', 'oneWeek': '46.2%', 'type': 'positive'},
                {'range': '3.25-3.50%', 'current': '38.1%', 'oneDay': '38.7%', 'oneWeek': '39.8%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '12.4%', 'oneDay': '12.2%', 'oneWeek': '12.8%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '1.1%', 'oneDay': '1.1%', 'oneWeek': '1.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.1%', 'oneDay': '0.1%', 'oneWeek': '0.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'}
            ]
        },
        '0000-00-00': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'}
            ]
        }
    }

def get_fomc_data():
    """FOMC会合日程データ（FedWatchセクション用）"""
    return {
        '2025-07-30': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '5.2%', 'oneWeek': '6.1%', 'oneMonth': '8.3%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '12.8%', 'oneWeek': '14.2%', 'oneMonth': '16.7%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '26.3%', 'oneMonth': '28.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '42.3%', 'oneWeek': '41.1%', 'oneMonth': '37.8%', 'type': 'positive'},
                {'range': '4.00-4.25%', 'current': '12.1%', 'oneWeek': '10.8%', 'oneMonth': '7.2%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '2.5%', 'oneWeek': '1.5%', 'oneMonth': '1.1%', 'type': 'negative'}
            ]
        },
        '2025-09-17': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '8.7%', 'oneWeek': '9.8%', 'oneMonth': '12.1%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '18.4%', 'oneWeek': '20.2%', 'oneMonth': '23.5%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '35.8%', 'oneWeek': '36.1%', 'oneMonth': '35.2%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '28.1%', 'oneWeek': '26.8%', 'oneMonth': '24.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '7.9%', 'oneWeek': '6.5%', 'oneMonth': '4.8%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '1.1%', 'oneWeek': '0.6%', 'oneMonth': '0.3%', 'type': 'negative'}
            ]
        },
        '2025-10-29': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '12.3%', 'oneWeek': '13.7%', 'oneMonth': '16.8%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '28.9%', 'oneWeek': '30.4%', 'oneMonth': '33.2%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '41.2%', 'oneWeek': '40.1%', 'oneMonth': '37.9%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '15.1%', 'oneWeek': '13.8%', 'oneMonth': '10.7%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '2.3%', 'oneWeek': '1.8%', 'oneMonth': '1.2%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.2%', 'type': 'negative'}
            ]
        },
        '2025-12-10': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '16.8%', 'oneWeek': '18.5%', 'oneMonth': '22.1%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '35.7%', 'oneWeek': '37.2%', 'oneMonth': '39.8%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '32.4%', 'oneWeek': '31.1%', 'oneMonth': '28.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '12.8%', 'oneWeek': '11.2%', 'oneMonth': '8.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '2.1%', 'oneWeek': '1.8%', 'oneMonth': '1.0%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.1%', 'type': 'negative'}
            ]
        },
        '2026-01-26': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '22.4%', 'oneWeek': '24.1%', 'oneMonth': '27.8%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '47.8%', 'oneWeek': '46.2%', 'oneMonth': '43.1%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '25.8%', 'oneMonth': '25.2%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '4.3%', 'oneWeek': '3.6%', 'oneMonth': '3.6%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.4%', 'oneWeek': '0.3%', 'oneMonth': '0.3%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'}
            ]
        },
        '2026-03-18': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '38.7%', 'oneWeek': '39.4%', 'oneMonth': '41.2%', 'type': 'positive'},
                {'range': '3.25-3.50%', 'current': '42.1%', 'oneWeek': '40.8%', 'oneMonth': '38.9%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '17.3%', 'oneWeek': '17.9%', 'oneMonth': '18.1%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '1.8%', 'oneWeek': '1.8%', 'oneMonth': '1.7%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'}
            ]
        },
        '2026-04-29': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '51.2%', 'oneWeek': '50.8%', 'oneMonth': '49.1%', 'type': 'positive'},
                {'range': '3.25-3.50%', 'current': '35.4%', 'oneWeek': '36.1%', 'oneMonth': '37.8%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '12.1%', 'oneWeek': '11.8%', 'oneMonth': '11.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '1.2%', 'oneWeek': '1.2%', 'oneMonth': '1.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'}
            ]
        },
        '2026-06-17': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '63.8%', 'oneWeek': '62.9%', 'oneMonth': '60.4%', 'type': 'positive'},
                {'range': '3.25-3.50%', 'current': '28.7%', 'oneWeek': '29.8%', 'oneMonth': '32.1%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '6.9%', 'oneWeek': '6.7%', 'oneMonth': '6.9%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '0.5%', 'oneWeek': '0.5%', 'oneMonth': '0.5%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'}
            ]
        },
        '0000-00-00': {
            'probabilities': [
                {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'},
                {'range': '3.25-3.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%', 'type': 'negative'}
            ]
        }
    }

def index(request):
    context = {
        'fed_monitor_data': json.dumps(get_fed_monitor_data()),
        'fomc_data': json.dumps(get_fomc_data()),
    }
    return render(request, 'control/index.html', context)