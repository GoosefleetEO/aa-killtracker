# Generated by Django 4.0.6 on 2022-07-15 19:03

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("eveuniverse", "0007_evetype_description"),
        ("killtracker", "0005_add_final_blow_clause_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="EveTypePlus",
            fields=[],
            options={
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("eveuniverse.evetype",),
        ),
    ]
