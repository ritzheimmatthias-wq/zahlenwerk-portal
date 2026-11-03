from django.db import migrations, models


class Migration(migrations.Migration):
    """Anspruchstoken für den Maileingang (Nachprüfung N2)."""

    dependencies = [
        ("kundenportal", "0003_integritaet_und_eingang"),
    ]

    operations = [
        migrations.AddField(
            model_name="maileingang",
            name="bearbeitung_token",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
