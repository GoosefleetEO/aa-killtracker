from django.contrib import admin
from django.db.models.functions import Lower

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveGroup, EveType

from .models import Webhook, Tracker
from . import tasks


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "is_enabled", "_messages_in_queue")
    list_filter = ("is_enabled",)

    def _messages_in_queue(self, obj):
        return obj.queue_size()

    actions = ["send_test_message", "purge_messages"]

    def purge_messages(self, request, queryset):
        actions_count = 0
        killmails_deleted = 0
        for webhook in queryset:
            killmails_deleted += webhook.clear_queue()
            actions_count += 1

        self.message_user(
            request,
            f"Purged queued messages for {actions_count} webhooks, "
            f"deleting a total of {killmails_deleted} messages.",
        )

    purge_messages.short_description = "Purge queued messages of selected webhooks"

    def send_test_message(self, request, queryset):
        actions_count = 0
        for webhook in queryset:
            tasks.send_test_message_to_webhook.delay(webhook.pk, request.user.pk)
            actions_count += 1

        self.message_user(
            request,
            f"Initiated sending of {actions_count} test messages to "
            f"selected webhooks. You will receive a notification with the result.",
        )

    send_test_message.short_description = "Send test message to selected webhooks"


@admin.register(Tracker)
class TrackerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_enabled",
        "origin_solar_system",
        "webhook",
    )
    list_filter = (
        "is_enabled",
        ("origin_solar_system", admin.RelatedOnlyFieldListFilter),
        ("webhook", admin.RelatedOnlyFieldListFilter),
    )

    autocomplete_fields = ["origin_solar_system"]

    filter_horizontal = (
        "exclude_attacker_alliances",
        "exclude_attacker_corporations",
        "require_attacker_alliances",
        "require_attacker_corporations",
        "require_victim_alliances",
        "require_victim_corporations",
        "require_regions",
        "require_constellations",
        "require_solar_systems",
        "require_attackers_ship_groups",
        "require_attackers_ship_types",
        "require_victim_ship_groups",
    )

    fieldsets = (
        (None, {"fields": ("name", "description", "is_enabled")}),
        (
            "Discord Configuration",
            {"fields": ("webhook", "ping_type", "is_posting_name",),},
        ),
        (
            "Locations",
            {
                "fields": (
                    "origin_solar_system",
                    "require_max_jumps",
                    "require_max_distance",
                    (
                        "exclude_low_sec",
                        "exclude_null_sec",
                        "exclude_w_space",
                        "exclude_high_sec",
                    ),
                    "require_regions",
                    "require_constellations",
                    "require_solar_systems",
                ),
            },
        ),
        (
            "Organizations",
            {
                "fields": (
                    "exclude_attacker_alliances",
                    "exclude_attacker_corporations",
                    "require_attacker_alliances",
                    "require_attacker_corporations",
                    "require_victim_alliances",
                    "require_victim_corporations",
                ),
            },
        ),
        (
            "Fleet detection",
            {
                "fields": (
                    "require_min_attackers",
                    "require_max_attackers",
                    "identify_fleets",
                ),
            },
        ),
        (
            "EveKillmail properties",
            {
                "fields": (
                    "require_min_value",
                    "exclude_npc_kills",
                    "require_npc_kills",
                ),
            },
        ),
        (
            "Ship types",
            {
                "fields": (
                    "require_attackers_ship_groups",
                    "require_attackers_ship_types",
                    "require_victim_ship_groups",
                ),
            },
        ),
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """overriding this formfield to have sorted lists in the form"""
        if db_field.name == "exclude_attacker_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "require_attacker_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "require_victim_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "require_attackers_ship_groups":
            kwargs["queryset"] = EveGroup.objects.all().order_by(Lower("name"))

        elif db_field.name == "require_attackers_ship_types":
            kwargs["queryset"] = EveType.objects.all().order_by(Lower("name"))

        return super().formfield_for_manytomany(db_field, request, **kwargs)
