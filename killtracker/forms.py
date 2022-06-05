from django import forms
from django.core.exceptions import ValidationError
from django.forms.widgets import TextInput
from django.utils.translation import gettext_lazy as _

from .models import Tracker


def field_nice_display(name: str) -> str:
    return name.replace("_", " ").capitalize()


class TrackerAdminKillmailIdForm(forms.Form):
    killmail_id = forms.IntegerField()


class TrackerAdminForm(forms.ModelForm):
    class Meta:
        model = Tracker
        fields = "__all__"
        widgets = {
            "color": TextInput(attrs={"type": "color"}),
        }

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
            cleaned_data,
            "exclude_attacker_alliances",
            "require_attacker_alliances",
        )

        self._validate_not_same_options_chosen(
            cleaned_data,
            "exclude_attacker_corporations",
            "require_attacker_corporations",
        )

        self._validate_not_same_options_chosen(
            cleaned_data,
            "exclude_victim_corporations",
            "require_victim_corporations",
        )

        self._validate_not_same_options_chosen(
            cleaned_data,
            "exclude_victim_alliances",
            "require_victim_alliances",
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

        if cleaned_data["require_attacker_organizations_final_blow"] and not (
            cleaned_data["require_attacker_alliances"]
            or cleaned_data["require_attacker_corporations"]
        ):
            raise ValidationError(
                {
                    "require_attacker_organizations_final_blow": _(
                        "You need to also require an attacker alliance "
                        "or attacker corporation."
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
                    for x in [
                        "exclude_npc_kills",
                        "require_npc_kills",
                    ]
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
                map(
                    str,
                    [display_func(x) for x in same_options],
                )
            )
            raise ValidationError(
                f"Can not choose same options for {field_nice_display(field_name_1)} "
                f"& {field_nice_display(field_name_2)}: {same_options_text}"
            )
