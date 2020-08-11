from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveGroup, EveType

from .core.killmails import Killmail
from .models import Webhook, Tracker
from . import tasks


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "is_enabled", "_messages_in_queue")
    list_filter = ("is_enabled",)
    ordering = ("name",)

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


class TrackerAdminKillmailId(forms.Form):
    killmail_id = forms.IntegerField()


def field_nice_display(name: str) -> str:
    return name.replace("_", " ").capitalize()


class TrackerAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()

        if (
            cleaned_data["require_max_jumps"]
            and not cleaned_data["origin_solar_system"]
        ):
            raise ValidationError(
                {
                    "origin_solar_system": _(
                        "'Require max jumps' needs an "
                        f"{field_nice_display('origin_solar_system')} to work"
                    )
                }
            )

        if (
            cleaned_data["require_max_distance"]
            and not cleaned_data["origin_solar_system"]
        ):
            raise ValidationError(
                {
                    "origin_solar_system": _(
                        "'Require max distance' needs an "
                        f"{field_nice_display('origin_solar_system')} to work"
                    )
                }
            )

        self._validate_not_same_options_chosen(
            cleaned_data, "exclude_attacker_alliances", "require_attacker_alliances",
        )
        self._validate_not_same_options_chosen(
            cleaned_data,
            "exclude_attacker_corporations",
            "require_attacker_corporations",
        )

        if (
            cleaned_data["require_min_attackers"]
            and cleaned_data["require_max_attackers"]
            and cleaned_data["require_min_attackers"]
            > cleaned_data["require_max_attackers"]
        ):
            raise ValidationError(
                {
                    "require_min_attackers": _(
                        "Can not be larger than "
                        f"{field_nice_display('require_max_attackers')}"
                    )
                }
            )

        if (
            cleaned_data["exclude_high_sec"]
            and cleaned_data["exclude_low_sec"]
            and cleaned_data["exclude_null_sec"]
            and cleaned_data["exclude_w_space"]
        ):
            text = ", ".join(
                [
                    field_nice_display(x)
                    for x in [
                        "exclude_low_sec",
                        "exclude_null_sec",
                        "exclude_w_space",
                        "exclude_high_sec",
                    ]
                ]
            )
            raise ValidationError(
                f"Setting all four clauses together does not make sense: {text}"
            )

        if cleaned_data["exclude_npc_kills"] and cleaned_data["require_npc_kills"]:
            text = ", ".join(
                [
                    field_nice_display(x)
                    for x in ["exclude_npc_kills", "require_npc_kills",]
                ]
            )
            raise ValidationError(
                f"Setting both clauses together does not make sense: {text}"
            )

    @staticmethod
    def _validate_not_same_options_chosen(
        cleaned_data, field_name_1, field_name_2, display_func=lambda x: x
    ) -> None:
        same_options = set(cleaned_data[field_name_1]).intersection(
            set(cleaned_data[field_name_2])
        )
        if same_options:
            same_options_text = ", ".join(
                map(str, [display_func(x) for x in same_options],)
            )
            raise ValidationError(
                f"Can not choose same options for {field_nice_display(field_name_1)} "
                f"& {field_nice_display(field_name_2)}: {same_options_text}"
            )


@admin.register(Tracker)
class TrackerAdmin(admin.ModelAdmin):
    form = TrackerAdminForm
    list_display = (
        "name",
        "is_enabled",
        "webhook",
        "identify_fleets",
        "_clauses",
    )
    list_filter = (
        "is_enabled",
        ("origin_solar_system", admin.RelatedOnlyFieldListFilter),
        ("webhook", admin.RelatedOnlyFieldListFilter),
    )
    ordering = ("name",)

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
            text = ", ".join(map(str, getattr(obj, field).all()))
            self._append_field_to_clauses(clauses, field, text)

    def _append_field_to_clauses(self, clauses, field, text):
        clauses.append(f"{field_nice_display(field)} = {text}")

    actions = ["run_test_killmail"]

    def run_test_killmail(self, request, queryset):
        if "apply" in request.POST:
            form = TrackerAdminKillmailId(request.POST)
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
        else:
            last_killmail_id = request.session.get("last_killmail_id")
            if last_killmail_id:
                initial = {"killmail_id": last_killmail_id}
            else:
                initial = None
            form = TrackerAdminKillmailId(initial=initial)

        return render(
            request,
            "admin/killtracker_killmail_id.html",
            {
                "form": form,
                "title": "Load Test Killmail for Tracker",
                "queryset": queryset.all(),
            },
        )

    run_test_killmail.short_description = "Run test killmail with selected trackers"

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
