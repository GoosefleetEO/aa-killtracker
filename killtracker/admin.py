from django.contrib import admin
from django.db.models import Max
from django.db.models.functions import Lower

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveSolarSystem, EveGroup

from .models import Killmail, Webhook, Tracker
from . import tasks

EVE_CATEGORY_ID_SHIPS = 6


@admin.register(EveSolarSystem)
class EveSolarSystemAdmin(admin.ModelAdmin):
    ordering = ["name"]
    search_fields = ["name"]

    def has_module_permission(self, request):
        return False


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


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "is_default",
    )


@admin.register(Tracker)
class Tracker(admin.ModelAdmin):
    list_display = (
        "name",
        "webhook",
        "_processed_count",
        "_matching_count",
        "_sent_count",
        "_last_sent",
    )
    autocomplete_fields = ["origin_solar_system"]

    def _processed_count(self, obj):
        return obj.trackerkillmail_set.count()

    def _matching_count(self, obj):
        return obj.trackerkillmail_set.filter(is_matching=True).count()

    def _sent_count(self, obj):
        return obj.trackerkillmail_set.filter(date_sent__isnull=False).count()

    def _last_sent(self, obj):
        result = obj.trackerkillmail_set.all().aggregate(Max("date_sent"))
        return result["date_sent__max"]

    actions = ["run_tracker"]

    def run_tracker(self, request, queryset):
        for tracker in queryset:
            tasks.run_tracker.delay(tracker.pk)

    filter_horizontal = (
        "exclude_attacker_alliances",
        "required_attacker_alliances",
        "require_victim_alliances",
        "require_attackers_ship_groups",
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """overriding this formfield to have sorted lists in the form"""
        if db_field.name == "exclude_attacker_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "required_attacker_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "require_victim_alliances":
            kwargs["queryset"] = EveAllianceInfo.objects.all().order_by(
                Lower("alliance_name")
            )
        elif db_field.name == "require_attackers_ship_groups":
            kwargs["queryset"] = EveGroup.objects.filter(
                id=EVE_CATEGORY_ID_SHIPS
            ).order_by(Lower("name"))
        return super().formfield_for_manytomany(db_field, request, **kwargs)
