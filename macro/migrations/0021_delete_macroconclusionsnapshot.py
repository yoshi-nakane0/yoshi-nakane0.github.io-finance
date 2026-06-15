from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0020_macroconclusionsnapshot_vintageobservation_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='MacroConclusionSnapshot',
        ),
    ]
