from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SectorSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sectors", models.JSONField()),
                ("benchmarks", models.JSONField()),
                ("update_time", models.CharField(max_length=32)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
