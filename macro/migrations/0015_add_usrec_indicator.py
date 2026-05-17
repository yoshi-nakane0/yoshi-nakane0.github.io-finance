from django.db import migrations


USREC = {
    'fred_series_id': 'USREC',
    'source': 'fred',
    'name_ja': '米景気後退フラグ',
    'name_en': 'NBER based Recession Indicators for the United States',
    'category': 'growth',
    'importance': 'C',
    'frequency': 'monthly',
    'unit': 'flag',
    'description': 'NBERの景気後退期を示す月次フラグ。バックテストの正解データとして使用する。',
    'display_order': 299,
}


def add_usrec(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    Indicator.objects.update_or_create(
        fred_series_id=USREC['fred_series_id'],
        defaults={
            'source': USREC['source'],
            'name_ja': USREC['name_ja'],
            'name_en': USREC['name_en'],
            'category': USREC['category'],
            'importance': USREC['importance'],
            'frequency': USREC['frequency'],
            'unit': USREC['unit'],
            'description': USREC['description'],
            'display_order': USREC['display_order'],
            'is_active': True,
            'judgment_rule': None,
        },
    )


def remove_usrec(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    Indicator.objects.filter(fred_series_id=USREC['fred_series_id']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0014_regimesnapshot_explainability'),
    ]

    operations = [
        migrations.RunPython(add_usrec, remove_usrec),
    ]
