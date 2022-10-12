# Generated by Django 4.0.7 on 2022-10-12 11:33
# Manually adopted to replace a squashed migration and be compatible with AA 2.x

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [
        ("killtracker", "0001_squashed_all"),
        ("killtracker", "0002_fix_webhook_notes_field"),
        ("killtracker", "0003_add_state_clauses"),
        ("killtracker", "0004_django4_update"),
        ("killtracker", "0005_add_final_blow_clause_and_more"),
        ("killtracker", "0006_evetypeplus"),
        ("killtracker", "0007_restructure_killsmails"),
        ("killtracker", "0008_copy_data_to_new_structure"),
        ("killtracker", "0009_remove_old_models"),
    ]

    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("authentication", "0019_merge_20211026_0919"),
        ("eveuniverse", "0007_evetype_description"),
        ("eveonline", "0015_factions"),
    ]

    operations = [
        migrations.CreateModel(
            name="EveKillmail",
            fields=[
                ("id", models.BigIntegerField(primary_key=True, serialize=False)),
                (
                    "time",
                    models.DateTimeField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "damage_taken",
                    models.BigIntegerField(blank=True, default=None, null=True),
                ),
                ("position_x", models.FloatField(blank=True, default=None, null=True)),
                ("position_y", models.FloatField(blank=True, default=None, null=True)),
                ("position_z", models.FloatField(blank=True, default=None, null=True)),
                (
                    "location_id",
                    models.PositiveIntegerField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                ("hash", models.CharField(blank=True, default="", max_length=64)),
                (
                    "fitted_value",
                    models.FloatField(blank=True, default=None, null=True),
                ),
                (
                    "total_value",
                    models.FloatField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "zkb_points",
                    models.PositiveIntegerField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "is_npc",
                    models.BooleanField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "is_solo",
                    models.BooleanField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "is_awox",
                    models.BooleanField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "alliance",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "character",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "corporation",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "faction",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "ship_type",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "solar_system",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="eveuniverse.eveentity",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="Webhook",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="short name to identify this webhook",
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "webhook_type",
                    models.IntegerField(
                        choices=[(1, "Discord Webhook")],
                        default=1,
                        help_text="type of this webhook",
                    ),
                ),
                (
                    "url",
                    models.CharField(
                        help_text="URL of this webhook, e.g. https://discordapp.com/api/webhooks/123456/abcdef",
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True,
                        help_text="you can add notes about this webhook here if you want",
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        help_text="whether notifications are currently sent to this webhook",
                    ),
                ),
            ],
        ),
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
        migrations.CreateModel(
            name="Tracker",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Name to identify tracker. Will be shown on alerts posts.",
                        max_length=100,
                        unique=True,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Brief description what this tracker is for. Will not be shown on alerts.",
                    ),
                ),
                (
                    "color",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional color for embed on Discord - #000000 / black means no color selected.",
                        max_length=7,
                    ),
                ),
                (
                    "require_max_jumps",
                    models.PositiveIntegerField(
                        blank=True,
                        default=None,
                        help_text="Require all killmails to be max x jumps away from origin solar system.",
                        null=True,
                    ),
                ),
                (
                    "require_max_distance",
                    models.FloatField(
                        blank=True,
                        default=None,
                        help_text="Require all killmails to be max x LY away from origin solar system.",
                        null=True,
                    ),
                ),
                (
                    "require_attacker_organizations_final_blow",
                    models.BooleanField(
                        blank=True,
                        default=False,
                        help_text="Only include killmails where at least one of the specified <b>required attacker corporations</b> or <b>required attacker alliances</b> has the final blow.",
                    ),
                ),
                (
                    "identify_fleets",
                    models.BooleanField(
                        default=False,
                        help_text="When true: kills are interpreted and shown as fleet kills.",
                    ),
                ),
                (
                    "exclude_blue_attackers",
                    models.BooleanField(
                        default=False,
                        help_text="Exclude killmails with blue attackers.",
                    ),
                ),
                (
                    "require_blue_victim",
                    models.BooleanField(
                        default=False,
                        help_text="Only include killmails where the victim has standing with our group.",
                    ),
                ),
                (
                    "require_min_attackers",
                    models.PositiveIntegerField(
                        blank=True,
                        default=None,
                        help_text="Require killmails to have at least given number of attackers.",
                        null=True,
                    ),
                ),
                (
                    "require_max_attackers",
                    models.PositiveIntegerField(
                        blank=True,
                        default=None,
                        help_text="Require killmails to have no more than max number of attackers.",
                        null=True,
                    ),
                ),
                (
                    "exclude_high_sec",
                    models.BooleanField(
                        default=False,
                        help_text="Exclude killmails from high sec. Also exclude high sec systems in route finder for jumps from origin.",
                    ),
                ),
                (
                    "exclude_low_sec",
                    models.BooleanField(
                        default=False, help_text="Exclude killmails from low sec."
                    ),
                ),
                (
                    "exclude_null_sec",
                    models.BooleanField(
                        default=False, help_text="Exclude killmails from null sec."
                    ),
                ),
                (
                    "exclude_w_space",
                    models.BooleanField(
                        default=False, help_text="Exclude killmails from WH space."
                    ),
                ),
                (
                    "require_min_value",
                    models.PositiveIntegerField(
                        blank=True,
                        default=None,
                        help_text="Require killmail's value to be greater or equal to the given value in M ISK.",
                        null=True,
                    ),
                ),
                (
                    "exclude_npc_kills",
                    models.BooleanField(default=False, help_text="Exclude npc kills."),
                ),
                (
                    "require_npc_kills",
                    models.BooleanField(
                        default=False,
                        help_text="Only include killmails that are npc kills.",
                    ),
                ),
                (
                    "ping_type",
                    models.CharField(
                        choices=[
                            ("PN", "(none)"),
                            ("PH", "@here"),
                            ("PE", "@everybody"),
                        ],
                        default="PN",
                        help_text="Option to ping every member of the channel.",
                        max_length=2,
                        verbose_name="channel pings",
                    ),
                ),
                (
                    "is_posting_name",
                    models.BooleanField(
                        default=True,
                        help_text="Whether posted messages include the tracker's name.",
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        help_text="Toogle for activating or deactivating a tracker.",
                    ),
                ),
                (
                    "exclude_attacker_alliances",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Exclude killmails with attackers from one of these alliances. ",
                        related_name="+",
                        to="eveonline.eveallianceinfo",
                    ),
                ),
                (
                    "exclude_attacker_corporations",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Exclude killmails with attackers from one of these corporations. ",
                        related_name="+",
                        to="eveonline.evecorporationinfo",
                    ),
                ),
                (
                    "exclude_attacker_states",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Exclude killmails with characters belonging to users with these Auth states. ",
                        related_name="+",
                        to="authentication.state",
                    ),
                ),
                (
                    "exclude_victim_alliances",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Exclude killmails where the victim belongs to one of these alliances. ",
                        related_name="+",
                        to="eveonline.eveallianceinfo",
                    ),
                ),
                (
                    "exclude_victim_corporations",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Exclude killmails where the victim belongs to one of these corporations. ",
                        related_name="+",
                        to="eveonline.evecorporationinfo",
                    ),
                ),
                (
                    "origin_solar_system",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        help_text="Solar system to calculate distance and jumps from. When provided distance and jumps will be shown on killmail messages.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_DEFAULT,
                        related_name="+",
                        to="eveuniverse.evesolarsystem",
                    ),
                ),
                (
                    "ping_groups",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Option to ping specific group members. ",
                        related_name="+",
                        to="auth.group",
                        verbose_name="group pings",
                    ),
                ),
                (
                    "require_attacker_alliances",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails with attackers from one of these alliances. ",
                        related_name="+",
                        to="eveonline.eveallianceinfo",
                    ),
                ),
                (
                    "require_attacker_corporations",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails with attackers from one of these corporations. ",
                        related_name="+",
                        to="eveonline.evecorporationinfo",
                    ),
                ),
                (
                    "require_attacker_states",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails with characters belonging to users with these Auth states. ",
                        related_name="+",
                        to="authentication.state",
                    ),
                ),
                (
                    "require_attackers_ship_groups",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where at least one attacker is flying one of these ship groups. ",
                        related_name="+",
                        to="eveuniverse.evegroup",
                    ),
                ),
                (
                    "require_attackers_ship_types",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where at least one attacker is flying one of these ship types. ",
                        related_name="+",
                        to="eveuniverse.evetype",
                    ),
                ),
                (
                    "require_constellations",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails that occurred in one of these regions. ",
                        related_name="+",
                        to="eveuniverse.eveconstellation",
                    ),
                ),
                (
                    "require_regions",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails that occurred in one of these regions. ",
                        related_name="+",
                        to="eveuniverse.everegion",
                    ),
                ),
                (
                    "require_solar_systems",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails that occurred in one of these regions. ",
                        related_name="+",
                        to="eveuniverse.evesolarsystem",
                    ),
                ),
                (
                    "require_victim_alliances",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where the victim belongs to one of these alliances. ",
                        related_name="+",
                        to="eveonline.eveallianceinfo",
                    ),
                ),
                (
                    "require_victim_corporations",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where the victim belongs to one of these corporations. ",
                        related_name="+",
                        to="eveonline.evecorporationinfo",
                    ),
                ),
                (
                    "require_victim_ship_groups",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where victim is flying one of these ship groups. ",
                        related_name="+",
                        to="eveuniverse.evegroup",
                    ),
                ),
                (
                    "require_victim_ship_types",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where victim is flying one of these ship types. ",
                        related_name="+",
                        to="eveuniverse.evetype",
                    ),
                ),
                (
                    "require_victim_states",
                    models.ManyToManyField(
                        blank=True,
                        default=None,
                        help_text="Only include killmails where the victim characters belong to users with these Auth states. ",
                        related_name="+",
                        to="authentication.state",
                    ),
                ),
                (
                    "webhook",
                    models.ForeignKey(
                        help_text="Webhook URL for a channel on Discord to sent all alerts to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="killtracker.webhook",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="EveKillmailAttacker",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "damage_done",
                    models.BigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "is_final_blow",
                    models.BooleanField(
                        blank=True, db_index=True, default=None, null=True
                    ),
                ),
                (
                    "security_status",
                    models.FloatField(blank=True, default=None, null=True),
                ),
                (
                    "alliance",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "character",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "corporation",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "faction",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "killmail",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attackers",
                        to="killtracker.evekillmail",
                    ),
                ),
                (
                    "ship_type",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
                (
                    "weapon_type",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
