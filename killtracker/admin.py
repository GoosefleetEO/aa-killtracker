from django.contrib import admin
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.safestring import mark_safe
from eveuniverse.models import EveGroup, EveType

from allianceauth.eveonline.models import EveAllianceInfo

from . import tasks
from .constants import (
    EVE_CATEGORY_ID_FIGHTER,
    EVE_CATEGORY_ID_SHIP,
    EVE_CATEGORY_ID_STRUCTURE,
    EVE_GROUP_MINING_DRONE,
    EVE_GROUP_ORBITAL_INFRASTRUCTURE,
)
from .core.killmails import Killmail
from .forms import TrackerAdminForm, TrackerAdminKillmailIdForm, field_nice_display
from .models import EveKillmail, EveKillmailAttacker, Tracker, Webhook


class EveKillmailAttackerAdminInline(admin.TabularInline):
    model = EveKillmailAttacker

    def get_queryset(self, *args, **kwargs):
        qs = super().get_queryset(*args, **kwargs)
        return qs.select_related(
            "character",
            "corporation",
            "alliance",
            "ship_type",
            "weapon_type",
            "faction",
        )

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False


@admin.register(EveKillmail)
class EveKillmailAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "time",
        "solar_system",
        "ship_type",
        "character",
        "corporation",
        "alliance",
        "_attacker_count",
    )
    list_filter = ("time",)
    inlines = (EveKillmailAttackerAdminInline,)
    ordering = ["-time"]
    search_fields = (
        "id",
        "solar_system__name",
        "character__name",
        "corporation__name",
        "alliance__name",
        "ship_type__name",
    )

    def get_queryset(self, *args, **kwargs):
        qs = super().get_queryset(*args, **kwargs)
        return qs.select_related(
            "solar_system",
            "ship_type",
            "character",
            "corporation",
            "alliance",
        ).annotate(attackers_count=Count("attackers"))

    def _attacker_count(self, obj) -> int:
        return obj.attackers_count

    def has_add_permission(self, *args, **kwargs) -> bool:
        return False

    def has_change_permission(self, *args, **kwargs) -> bool:
        return False


@admin.register(Tracker)
class TrackerAdmin(admin.ModelAdmin):
    form = TrackerAdminForm
    list_display = (
        "name",
        "is_enabled",
        "webhook",
        "identify_fleets",
        "_clauses",
        "_pings",
        "_color",
    )
    list_filter = (
        "is_enabled",
        ("origin_solar_system", admin.RelatedOnlyFieldListFilter),
        ("webhook", admin.RelatedOnlyFieldListFilter),
    )
    ordering = ("name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related(
            "exclude_attacker_alliances",
            "require_attacker_alliances",
            "exclude_attacker_corporations",
            "require_attacker_corporations",
            "exclude_attacker_states",
            "require_attacker_states",
            "require_victim_states",
            "require_victim_alliances",
            "require_victim_corporations",
            "require_regions",
            "require_constellations",
            "require_solar_systems",
            "require_attackers_ship_groups",
            "require_attackers_ship_types",
            "require_victim_ship_groups",
            "require_victim_ship_types",
            "ping_groups",
        )

    def _color(self, obj):
        html = (
            f'<input type="color" value="{obj.color}" disabled>' if obj.color else "-"
        )
        return mark_safe(html)

    def _pings(self, obj):
        parts = [f"@{group.name}" for group in obj.ping_groups.all()]
        if obj.ping_type != Tracker.ChannelPingType.NONE:
            parts.append(obj.get_ping_type_display())

        return sorted(parts, key=str.casefold) if parts else None

    def _clauses(self, obj):
        clauses = list()
        for field, func in [
            ("origin_solar_system", self._add_to_clauses_1),
            ("require_max_jumps", self._add_to_clauses_1),
            ("require_max_distance", self._add_to_clauses_1),
            ("exclude_attacker_alliances", self._add_to_clauses_2),
            ("exclude_attacker_corporations", self._add_to_clauses_2),
            ("require_attacker_alliances", self._add_to_clauses_2),
            ("require_attacker_corporations", self._add_to_clauses_2),
            ("require_victim_alliances", self._add_to_clauses_2),
            ("require_victim_corporations", self._add_to_clauses_2),
            ("exclude_attacker_states", self._add_to_clauses_2),
            ("require_attacker_states", self._add_to_clauses_2),
            ("require_victim_states", self._add_to_clauses_2),
            ("exclude_blue_attackers", self._add_to_clauses_1),
            ("require_blue_victim", self._add_to_clauses_1),
            ("require_min_attackers", self._add_to_clauses_1),
            ("require_max_attackers", self._add_to_clauses_1),
            ("exclude_high_sec", self._add_to_clauses_1),
            ("exclude_low_sec", self._add_to_clauses_1),
            ("exclude_null_sec", self._add_to_clauses_1),
            ("exclude_w_space", self._add_to_clauses_1),
            ("require_regions", self._add_to_clauses_2),
            ("require_constellations", self._add_to_clauses_2),
            ("require_solar_systems", self._add_to_clauses_2),
            ("require_min_value", self._add_to_clauses_1),
            ("require_attackers_ship_groups", self._add_to_clauses_2),
            ("require_attackers_ship_types", self._add_to_clauses_2),
            ("require_victim_ship_groups", self._add_to_clauses_2),
            ("require_victim_ship_types", self._add_to_clauses_2),
            ("exclude_npc_kills", self._add_to_clauses_1),
            ("require_npc_kills", self._add_to_clauses_1),
        ]:
            func(clauses, obj, field)
        return mark_safe("<br>".join(clauses)) if clauses else None

    def _add_to_clauses_1(self, clauses, obj, field_name):
        field = getattr(obj, field_name)
        if field:
            self._append_field_to_clauses(clauses, field_name, getattr(obj, field_name))

    def _add_to_clauses_2(self, clauses, obj, field):
        if getattr(obj, field).count() > 0:
            text = ", ".join(sorted(map(str, getattr(obj, field).all())))
            self._append_field_to_clauses(clauses, field, text)

    def _append_field_to_clauses(self, clauses, field, text):
        clauses.append(f"{field_nice_display(field)} = {text}")

    actions = ["disable_tracker", "enable_tracker", "reset_color", "run_test_killmail"]

    @admin.display(description="Reset color for selected trackers")
    def reset_color(self, request, queryset):
        queryset.update(color="")

    @admin.display(description="Enable selected trackers")
    def enable_tracker(self, request, queryset):
        queryset.update(is_enabled=True)
        self.message_user(request, f"{queryset.count()} trackers enabled.")

    @admin.display(description="Disable selected trackers")
    def disable_tracker(self, request, queryset):
        queryset.update(is_enabled=False)
        self.message_user(request, f"{queryset.count()} trackers disabled.")

    @admin.display(description="Run test killmail with selected trackers")
    def run_test_killmail(self, request, queryset):
        if "apply" in request.POST:
            form = TrackerAdminKillmailIdForm(request.POST)
            if form.is_valid():
                killmail_id = form.cleaned_data["killmail_id"]
                killmail = Killmail.create_from_zkb_api(killmail_id)
                if killmail:
                    request.session["last_killmail_id"] = killmail_id
                    actions_count = 0
                    for tracker in queryset:
                        tasks.run_tracker.delay(
                            tracker_pk=tracker.pk,
                            killmail_json=killmail.asjson(),
                            ignore_max_age=True,
                        )
                        actions_count += 1
                    self.message_user(
                        request,
                        (
                            f"Started {actions_count} tracker(s) for "
                            f"killmail with ID {killmail_id}."
                        ),
                    )
                else:
                    self.message_user(
                        request,
                        "Failed to load killmail with ID {killmail_id} from ZKB",
                    )
            return HttpResponseRedirect(request.get_full_path())

        last_killmail_id = request.session.get("last_killmail_id")
        if last_killmail_id:
            initial = {"killmail_id": last_killmail_id}
        else:
            initial = None
        form = TrackerAdminKillmailIdForm(initial=initial)
        return render(
            request,
            "admin/killtracker_killmail_id.html",
            {
                "form": form,
                "title": "Load Test Killmail for Tracker",
                "queryset": queryset.all(),
            },
        )

    autocomplete_fields = ["origin_solar_system"]

    filter_horizontal = (
        "exclude_attacker_alliances",
        "exclude_attacker_corporations",
        "require_attacker_alliances",
        "require_attacker_corporations",
        "require_victim_alliances",
        "require_victim_corporations",
        "exclude_attacker_states",
        "require_attacker_states",
        "require_victim_states",
        "require_regions",
        "require_constellations",
        "require_solar_systems",
        "require_attackers_ship_groups",
        "require_attackers_ship_types",
        "require_victim_ship_groups",
        "require_victim_ship_types",
        "ping_groups",
    )

    fieldsets = (
        (None, {"fields": ("name", "description", "is_enabled", "color")}),
        (
            "Discord Configuration",
            {
                "fields": (
                    "webhook",
                    "ping_type",
                    "ping_groups",
                    "is_posting_name",
                ),
            },
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
            "Auth State",
            {
                "fields": (
                    "exclude_attacker_states",
                    "require_attacker_states",
                    "require_victim_states",
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
                    "require_victim_ship_types",
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
            kwargs["queryset"] = EveGroup.objects.filter(
                eve_category_id__in=[
                    EVE_CATEGORY_ID_STRUCTURE,
                    EVE_CATEGORY_ID_SHIP,
                    EVE_CATEGORY_ID_FIGHTER,
                ],
                published=True,
            ).order_by(Lower("name"))
        elif db_field.name == "require_attackers_ship_types":
            kwargs["queryset"] = (
                EveType.objects.select_related("eve_group")
                .filter(
                    eve_group__eve_category_id__in=[
                        EVE_CATEGORY_ID_STRUCTURE,
                        EVE_CATEGORY_ID_SHIP,
                        EVE_CATEGORY_ID_FIGHTER,
                    ],
                    published=True,
                )
                .order_by(Lower("name"))
            )
        elif db_field.name == "require_victim_ship_groups":
            kwargs["queryset"] = EveGroup.objects.filter(
                (
                    Q(
                        eve_category_id__in=[
                            EVE_CATEGORY_ID_STRUCTURE,
                            EVE_CATEGORY_ID_SHIP,
                            EVE_CATEGORY_ID_FIGHTER,
                        ]
                    )
                    & Q(published=True)
                )
                | (Q(id=EVE_GROUP_MINING_DRONE) & Q(published=True))
                | Q(id=EVE_GROUP_ORBITAL_INFRASTRUCTURE)
            ).order_by(Lower("name"))

        elif db_field.name == "require_victim_ship_types":
            kwargs["queryset"] = (
                EveType.objects.select_related("eve_group")
                .filter(
                    (
                        Q(
                            eve_group__eve_category_id__in=[
                                EVE_CATEGORY_ID_STRUCTURE,
                                EVE_CATEGORY_ID_SHIP,
                                EVE_CATEGORY_ID_FIGHTER,
                            ]
                        )
                        & Q(published=True)
                    )
                    | (Q(eve_group_id=EVE_GROUP_MINING_DRONE) & Q(published=True))
                    | Q(eve_group_id=EVE_GROUP_ORBITAL_INFRASTRUCTURE)
                )
                .order_by(Lower("name"))
            )

        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "is_enabled", "_messages_in_queue")
    list_filter = ("is_enabled",)
    ordering = ("name",)

    def _messages_in_queue(self, obj):
        return obj.main_queue.size()

    actions = ["send_test_message", "purge_messages"]

    @admin.display(description="Purge queued messages of selected webhooks")
    def purge_messages(self, request, queryset):
        actions_count = 0
        killmails_deleted = 0
        for webhook in queryset:
            killmails_deleted += webhook.main_queue.clear()
            actions_count += 1
        self.message_user(
            request,
            f"Purged queued messages for {actions_count} webhooks, "
            f"deleting a total of {killmails_deleted} messages.",
        )

    @admin.display(description="Send test message to selected webhooks")
    def send_test_message(self, request, queryset):
        actions_count = 0
        for webhook in queryset:
            tasks.send_test_message_to_webhook.delay(webhook.pk)
            actions_count += 1
        self.message_user(
            request,
            f"Initiated sending of {actions_count} test messages to selected webhooks.",
        )
