"""Copy killmail data from old structure to new."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import migrations


def copy_killmails(apps, schema_editor):
    EveKillmail = apps.get_model("killtracker", "EveKillmail")
    print(f"Processing {EveKillmail.objects.count():,} killmails...", end="")
    objs = []
    for killmail in EveKillmail.objects.iterator():
        # victim
        try:
            killmail.character = killmail.victim.character
            killmail.corporation = killmail.victim.corporation
            killmail.alliance = killmail.victim.alliance
            killmail.faction = killmail.victim.faction
            killmail.ship_type = killmail.victim.ship_type
            killmail.damage_taken = killmail.victim.damage_taken
        except ObjectDoesNotExist:
            pass
        # position
        try:
            killmail.position_x = killmail.position.x
            killmail.position_y = killmail.position.y
            killmail.position_z = killmail.position.z
        except ObjectDoesNotExist:
            pass
        # zkb
        try:
            killmail.location_id = killmail.zkb.location_id
            killmail.hash = killmail.zkb.hash
            killmail.fitted_value = killmail.zkb.fitted_value
            killmail.total_value = killmail.zkb.total_value
            killmail.zkb_points = killmail.zkb.points
            killmail.is_npc = killmail.zkb.is_npc
            killmail.is_solo = killmail.zkb.is_solo
            killmail.is_awox = killmail.zkb.is_awox
        except ObjectDoesNotExist:
            pass
        objs.append(killmail)
    EveKillmail.objects.bulk_update(
        objs,
        [
            "character",
            "corporation",
            "alliance",
            "faction",
            "ship_type",
            "damage_taken",
            "position_x",
            "position_y",
            "position_z",
            "location_id",
            "hash",
            "fitted_value",
            "total_value",
            "zkb_points",
            "is_npc",
            "is_solo",
            "is_awox",
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0007_restructure_killsmails"),
    ]

    operations = [
        migrations.RunPython(copy_killmails, migrations.RunPython.noop),
    ]
