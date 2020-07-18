from django.contrib import admin
from django.db.models import Max
from django.db.models.functions import Lower

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveGroup, EveType

from .models import Killmail, Webhook, Tracker
from . import tasks


@admin.register(Killmail)
class KillmailAdmin(admin.ModelAdmin):
    list_select_related = True
    list_display = (
        "id",
        "time",
        "solar_system",
        "_victim_ship_type",
        "victim",
    )

    def _victim_ship_type(self, obj):
        return obj.victim.ship_type

    _victim_ship_type.admin_order_field = "victim__ship_type__name"

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_enabled",
    )
    list_filter = ("is_enabled",)

    actions = ["send_test_message"]

    def send_test_message(self, request, queryset):
        actions_count = 0
        for webhook in queryset:
            tasks.send_test_message_to_webhook(webhook.pk, request.user.pk)
            actions_count += 1

        self.message_user(
            request,
            f"Initiated sending of {actions_count} test messages to "
            f"selected webhooks. You will receive a notification with the result",
        )

    send_test_message.short_description = "Send test message to selected webhooks"


@admin.register(Tracker)
class TrackerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_enabled",
        "origin_solar_system",
        "webhook",
        "_processed_count",
        "_matching_count",
        "_sent_count",
        "_last_sent",
    )
    list_filter = (
        "is_enabled",
        ("origin_solar_system", admin.RelatedOnlyFieldListFilter),
        ("webhook", admin.RelatedOnlyFieldListFilter),
    )

    autocomplete_fields = ["origin_solar_system"]

    exclude = (
        "identify_fleets",
        "exclude_blue_attackers",
        "require_blue_victim",
    )

    def _processed_count(self, obj):
        return obj.trackedkillmail_set.count()

    def _matching_count(self, obj):
        return obj.trackedkillmail_set.filter(is_matching=True).count()

    def _sent_count(self, obj):
        return obj.trackedkillmail_set.filter(date_sent__isnull=False).count()

    def _last_sent(self, obj):
        result = obj.trackedkillmail_set.all().aggregate(Max("date_sent"))
        return result["date_sent__max"]

    actions = ["run_tracker"]

    def run_tracker(self, request, queryset):
        actions_count = 0
        for tracker in queryset:
            tasks.run_tracker.delay(tracker.pk)
            actions_count += 1

        self.message_user(request, f"Started {actions_count} trackers.")

    run_tracker.short_description = "Run selected trackers"

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
        (None, {"fields": ("name", "description")}),
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
            "Attacker counts",
            {"fields": ("require_min_attackers", "require_max_attackers",),},
        ),
        (
            "Killmail properties",
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
