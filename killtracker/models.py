from datetime import timedelta
import json
from time import sleep
from urllib.parse import urljoin

import dhooks_lite

from django.core.exceptions import ValidationError
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

from eveuniverse.models import (
    EveConstellation,
    EveRegion,
    EveSolarSystem,
    EveGroup,
    EveEntity,
    EveType,
)

from . import __title__
from .app_settings import KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
from .managers import KillmailManager, TrackedKillmailManager
from .utils import LoggerAddTag, get_site_base_url

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

DEFAULT_MAX_AGE_HOURS = 4
EVE_CATEGORY_ID_SHIP = 6
EVE_CATEGORY_ID_STRUCTURE = 65


# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
DISCORD_SEND_DELAY = 2


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (("basic_access", "Can access this app"),)


class Killmail(models.Model):

    ZKB_KILLMAIL_BASEURL = "https://zkillboard.com/kill/"

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    solar_system = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )

    objects = KillmailManager()

    def __str__(self):
        return f"ID:{self.id}"

    def __repr__(self):
        return f"Killmail(id={self.id})"

    def lod_entities(self):
        """loads unknown entities for this killmail"""
        qs = EveEntity.objects.filter(id__in=self.entity_ids(), name="")
        qs.update_from_esi()

    def entity_ids(self) -> list:
        ids = [
            self.victim.character_id,
            self.victim.corporation_id,
            self.victim.alliance_id,
            self.victim.ship_type_id,
            self.solar_system_id,
        ]
        for attacker in self.attackers.all():
            ids += [
                attacker.character_id,
                attacker.corporation_id,
                attacker.alliance_id,
                attacker.ship_type_id,
                attacker.weapon_type_id,
            ]
        return [x for x in ids if x is not None]


class KillmailCharacter(models.Model):

    character = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_characters_set",
    )
    corporation = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_corporations_set",
    )
    alliance = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_alliances_set",
    )
    faction = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_factions_set",
    )
    ship_type = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_shiptypes_set",
    )

    class Meta:
        abstract = True

    def __str__(self):
        if self.character:
            return str(self.character)
        elif self.corporation:
            return str(self.corporation)
        elif self.faction:
            return str(self.faction)
        else:
            return f"PK:{self.pk}"


class KillmailVictim(KillmailCharacter):

    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name="victim"
    )
    damage_taken = models.BigIntegerField(default=None, null=True, blank=True)


class KillmailAttacker(KillmailCharacter):

    killmail = models.ForeignKey(
        Killmail, on_delete=models.CASCADE, related_name="attackers"
    )
    damage_done = models.BigIntegerField(default=None, null=True, blank=True)
    is_final_blow = models.BooleanField(default=None, null=True, blank=True)
    security_status = models.FloatField(default=None, null=True, blank=True)
    weapon_type = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )


class KillmailPosition(models.Model):
    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name="position"
    )
    x = models.FloatField(default=None, null=True, blank=True)
    y = models.FloatField(default=None, null=True, blank=True)
    z = models.FloatField(default=None, null=True, blank=True)


class KillmailZkb(models.Model):

    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name="zkb"
    )
    location_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    hash = models.CharField(max_length=64, default="", blank=True)
    fitted_value = models.FloatField(default=None, null=True, blank=True)
    total_value = models.FloatField(default=None, null=True, blank=True)
    points = models.PositiveIntegerField(default=None, null=True, blank=True)
    is_npc = models.BooleanField(default=None, null=True, blank=True)
    is_solo = models.BooleanField(default=None, null=True, blank=True)
    is_awox = models.BooleanField(default=None, null=True, blank=True)


class Webhook(models.Model):
    """A destination for forwarding killmails"""

    TYPE_DISCORD = 1

    TYPE_CHOICES = [
        (TYPE_DISCORD, _("Discord Webhook")),
    ]

    name = models.CharField(
        max_length=64, unique=True, help_text="short name to identify this webhook"
    )
    webhook_type = models.IntegerField(
        choices=TYPE_CHOICES, default=TYPE_DISCORD, help_text="type of this webhook"
    )
    url = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "URL of this webhook, e.g. "
            "https://discordapp.com/api/webhooks/123456/abcdef"
        ),
    )
    notes = models.TextField(
        null=True,
        default=None,
        blank=True,
        help_text="you can add notes about this webhook here if you want",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="whether notifications are currently sent to this webhook",
    )

    def __str__(self):
        return self.name

    def __repr__(self):
        return "{}(id={}, name='{}')".format(
            self.__class__.__name__, self.id, self.name
        )

    def send_test_notification(self) -> tuple:
        """Sends a test notification to this webhook and returns send report"""
        hook = dhooks_lite.Webhook(self.url)
        response = hook.execute(
            content=_(
                "This is a test notification from %s.\n"
                "The webhook appears to be correctly configured."
            )
            % __title__,
            username=self.default_username(),
            avatar_url=self.default_avatar_url(),
            wait_for_response=True,
        )
        if response.status_ok:
            success = True
            send_report = json.dumps(response.content, indent=4, sort_keys=True)
        else:
            success = False
            send_report = "HTTP status code {}. Please see log for details".format(
                response.status_code
            )
        return send_report, success

    @staticmethod
    def default_avatar_url():
        """avatar url for all messages"""
        return urljoin(
            get_site_base_url(),
            staticfiles_storage.url("killtracker/killtracker_logo.png"),
        )

    @staticmethod
    def zkb_icon_url():
        """avatar url for all messages"""
        return urljoin(
            get_site_base_url(), staticfiles_storage.url("killtracker/zkb_icon.png"),
        )

    @staticmethod
    def default_username():
        """avatar username for all messages"""
        return __title__


class Tracker(models.Model):

    PING_TYPE_NONE = "PN"
    PING_TYPE_HERE = "PH"
    PING_TYPE_EVERYBODY = "PE"
    PING_TYPE_CHOICES = (
        (PING_TYPE_NONE, "(no ping)"),
        (PING_TYPE_HERE, "@here"),
        (PING_TYPE_EVERYBODY, "@everybody"),
    )

    name = models.CharField(
        max_length=100,
        help_text="name to identify tracker. Will be shown on alerts posts.",
        unique=True,
    )
    description = models.TextField(
        default="",
        blank=True,
        help_text=(
            "Brief description what this tracker is for. Will not be shown on alerts."
        ),
    )
    origin_solar_system = models.ForeignKey(
        EveSolarSystem,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="tracker_origin_solar_systems_set",
        help_text=(
            "Solar system to calculate distance and jumps from. "
            "When provided distance and jumps will be shown on killmail messages"
        ),
    )
    require_max_jumps = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Require all killmails to be max x jumps away from origin solar system"
        ),
    )
    require_max_distance = models.FloatField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Require all killmails to be max x LY away from origin solar system"
        ),
    )
    exclude_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="tracker_exclude_attacker_alliances_set",
        default=None,
        blank=True,
        help_text="exclude killmails with attackers from one of these alliances",
    )
    exclude_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="tracker_exclude_attacker_corporations_set",
        default=None,
        blank=True,
        help_text="exclude killmails with attackers from one of these corporations",
    )
    require_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="tracker_required_attacker_alliances_set",
        default=None,
        blank=True,
        help_text="only include killmails with attackers from one of these alliances",
    )
    require_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="tracker_required_attacker_corporations_set",
        default=None,
        blank=True,
        help_text="only include killmails with attackers from one of these corporations",
    )
    require_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="tracker_require_victim_alliances_set",
        default=None,
        blank=True,
        help_text=(
            "only include killmails where the victim belongs to one of these alliances"
        ),
    )
    require_victim_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="tracker_require_victim_corporations_set",
        default=None,
        blank=True,
        help_text=(
            "only include killmails where the victim belongs "
            "to one of these corporations"
        ),
    )
    identify_fleets = models.BooleanField(
        default=False,
        help_text="when true: kills are interpreted and shown as fleet kills",
    )
    exclude_blue_attackers = models.BooleanField(
        default=False, help_text=("exclude killmails with blue attackers"),
    )
    require_blue_victim = models.BooleanField(
        default=False,
        help_text=(
            "only include killmails where the victim has standing with our group"
        ),
    )
    require_min_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have at least given number of attackers",
    )
    require_max_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have no more than max number of attackers",
    )
    exclude_high_sec = models.BooleanField(
        default=False,
        help_text=(
            "exclude killmails from high sec. "
            "Also exclude high sec systems in route finder for jumps from origin."
        ),
    )
    exclude_low_sec = models.BooleanField(
        default=False, help_text="exclude killmails from low sec"
    )
    exclude_null_sec = models.BooleanField(
        default=False, help_text="exclude killmails from null sec"
    )
    exclude_w_space = models.BooleanField(
        default=False, help_text="exclude killmails from WH space"
    )
    require_regions = models.ManyToManyField(
        EveRegion,
        default=None,
        blank=True,
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_constellations = models.ManyToManyField(
        EveConstellation,
        default=None,
        blank=True,
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_solar_systems = models.ManyToManyField(
        EveSolarSystem,
        default=None,
        blank=True,
        related_name="tracker_require_solar_systems_set",
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_min_value = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have at least given value in ISK",
    )
    require_attackers_ship_groups = models.ManyToManyField(
        EveGroup,
        limit_choices_to=(
            Q(eve_category_id=EVE_CATEGORY_ID_STRUCTURE)
            | Q(eve_category_id=EVE_CATEGORY_ID_SHIP)
        )
        & Q(published=True),
        related_name="tracker_require_attackers_ship_groups_set",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship groups"
        ),
    )
    require_attackers_ship_types = models.ManyToManyField(
        EveType,
        limit_choices_to=(
            Q(eve_group__eve_category_id=EVE_CATEGORY_ID_STRUCTURE)
            | Q(eve_group__eve_category_id=EVE_CATEGORY_ID_SHIP)
        )
        & Q(published=True),
        related_name="tracker_require_attackers_ship_groups_set",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship types"
        ),
    )
    require_victim_ship_groups = models.ManyToManyField(
        EveGroup,
        limit_choices_to=(
            Q(eve_category_id=EVE_CATEGORY_ID_STRUCTURE)
            | Q(eve_category_id=EVE_CATEGORY_ID_SHIP)
        )
        & Q(published=True),
        related_name="tracker_require_victim_ship_groups_set",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where victim is flying one of these ship groups"
        ),
    )
    exclude_npc_kills = models.BooleanField(
        default=False, help_text="exclude npc kills"
    )
    require_npc_kills = models.BooleanField(
        default=False, help_text="only include killmails that are npc kills"
    )
    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.CASCADE,
        help_text="Webhook URL for a channel on Discord to sent all alerts to",
    )
    ping_type = models.CharField(
        max_length=2,
        choices=PING_TYPE_CHOICES,
        default=PING_TYPE_NONE,
        help_text="Options for pinging on every matching killmail",
    )
    is_posting_name = models.BooleanField(
        default=True, help_text="whether posted messages include the tracker's name"
    )
    is_enabled = models.BooleanField(
        default=True, help_text="toogle for activating or deactivating a tracker"
    )

    def __str__(self):
        return self.name

    def clean(self):
        if self.require_max_jumps and self.origin_solar_system is None:
            raise ValidationError(
                {
                    "origin_solar_system": _(
                        "'require max jumps' needs an origin solar system to work"
                    )
                }
            )

        if self.require_max_distance and self.origin_solar_system is None:
            raise ValidationError(
                {
                    "origin_solar_system": _(
                        "'require max distance' needs an origin solar system to work"
                    )
                }
            )

    def calculate_killmails(self, force: bool = False) -> int:
        """marks all killmails that comply with all criteria of this tracker
        and also adds additional information to killmails, like distance from origin
        
        Params:
        - force: run again for already processed killmails

        Returns the number of matching killmails
        """
        killmail_count = TrackedKillmail.objects.generate(tracker=self, force=force)
        logger.info("Tracker %s: Processing %d fresh killmails", self, killmail_count)

        # apply all filters from tracker to determine matching killmails

        threshold_date = now() - timedelta(
            hours=KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
        )
        matching_qs = (
            TrackedKillmail.objects.filter(tracker=self, is_matching__isnull=True)
            .exclude(killmail__time__lt=threshold_date)
            .select_related(
                "killmail__victim",
                "killmail__solar_system",
                "killmail__zkb",
                "solar_system",
            )
            .prefetch_related("killmail__attackers")
        )
        logger.debug(
            "Tracker %s: Processing fresh killmail IDs: %s",
            self,
            matching_qs.killmail_ids(),
        )

        if self.exclude_high_sec:
            matching_qs = matching_qs.exclude(is_high_sec=True)

        if self.exclude_low_sec:
            matching_qs = matching_qs.exclude(is_low_sec=True)

        if self.exclude_null_sec:
            matching_qs = matching_qs.exclude(is_null_sec=True)

        if self.exclude_w_space:
            matching_qs = matching_qs.exclude(is_w_space=True)

        if self.require_min_attackers:
            matching_qs = matching_qs.exclude(
                attackers_count__lt=self.require_min_attackers
            )

        if self.require_max_attackers:
            matching_qs = matching_qs.exclude(
                attackers_count__gt=self.require_max_attackers
            )

        if self.exclude_npc_kills:
            matching_qs = matching_qs.exclude(killmail__zkb__is_npc=True)

        if self.require_npc_kills:
            matching_qs = matching_qs.filter(killmail__zkb__is_npc=True)

        if self.require_min_value:
            matching_qs = matching_qs.exclude(
                killmail__zkb__total_value__lt=self.require_min_value
            )

        if self.require_max_distance:
            matching_qs = matching_qs.exclude(distance__isnull=True).exclude(
                distance__gt=self.require_max_distance
            )

        if self.require_max_jumps:
            matching_qs = matching_qs.exclude(jumps__isnull=True).exclude(
                jumps__gt=self.require_max_jumps
            )

        if self.require_regions.count() > 0:
            matching_qs = matching_qs.filter(
                solar_system__eve_constellation__eve_region__in=self.require_regions.all()
            )

        if self.require_constellations.count() > 0:
            matching_qs = matching_qs.filter(
                solar_system__eve_constellation__in=self.require_constellations.all()
            )

        if self.require_solar_systems.count() > 0:
            matching_qs = matching_qs.filter(
                solar_system__in=self.require_solar_systems.all()
            )

        if self.exclude_attacker_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.exclude_attacker_alliances)
            matching_qs = matching_qs.exclude(
                killmail__attackers__alliance__id__in=alliance_ids
            )

        if self.exclude_attacker_corporations.count() > 0:
            corporation_ids = self._extract_corporation_ids(
                self.exclude_attacker_corporations
            )
            matching_qs = matching_qs.exclude(
                killmail__attackers__corporation__id__in=corporation_ids
            )

        if self.require_attacker_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.require_attacker_alliances)
            matching_qs = matching_qs.filter(
                killmail__attackers__alliance__id__in=alliance_ids
            )

        if self.require_attacker_corporations.count() > 0:
            corporation_ids = self._extract_corporation_ids(
                self.require_attacker_corporations
            )
            matching_qs = matching_qs.filter(
                killmail__attackers__corporation__id__in=corporation_ids
            )

        if self.require_victim_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.require_victim_alliances)
            matching_qs = matching_qs.filter(
                killmail__victim__alliance__id__in=alliance_ids
            )

        if self.require_victim_corporations.count() > 0:
            corporation_ids = self._extract_corporation_ids(
                self.require_victim_corporations
            )
            matching_qs = matching_qs.filter(
                killmail__victim__corporation__id__in=corporation_ids
            )

        if self.require_victim_ship_groups.count() > 0:
            matching_qs = matching_qs.filter(
                victim_ship_type__eve_group__in=self.require_victim_ship_groups.all()
            )

        if self.require_attackers_ship_groups.count() > 0:
            matching_qs = matching_qs.filter(
                attackers_ship_types__eve_group__in=self.require_attackers_ship_groups.all()
            )

        if self.require_attackers_ship_types.count() > 0:
            matching_qs = matching_qs.filter(
                attackers_ship_types__in=self.require_attackers_ship_types.all()
            )

        # store which killmails match with this tracker
        logger.debug(
            "Tracker %s: Matching killmail IDs: %s", self, matching_qs.killmail_ids(),
        )
        matching_count = matching_qs.update(is_matching=True)
        logger.info(
            "Tracker %s: Found %d matching killmails", self, matching_count,
        )
        return matching_count

    @staticmethod
    def _extract_alliance_ids(alliances: models.QuerySet) -> list:
        return [
            int(alliance_id)
            for alliance_id in alliances.values_list("alliance_id", flat=True)
        ]

    @staticmethod
    def _extract_corporation_ids(corporations: models.QuerySet) -> list:
        return [
            int(corporation_id)
            for corporation_id in corporations.values_list("corporation_id", flat=True)
        ]

    def send_matching_to_webhook(self, resend: bool = False) -> int:
        """sends all matching killmails for this tracker to the webhook
        
        returns number of successfull sent messages
        """
        if not self.webhook.is_enabled:
            logger.info("Tracker %s: Webhook disabled - skipping sending", self)
            return 0

        matching_killmails_qs = (
            TrackedKillmail.objects.filter(tracker=self, is_matching=True)
            .prefetch_related()
            .order_by("killmail__time")
        )
        if not resend:
            matching_killmails_qs = matching_killmails_qs.filter(date_sent__isnull=True)

        logger.info(
            "Tracker %s: Found %d killmails to sent to webhook",
            self,
            matching_killmails_qs.count(),
        )
        killmail_counter = 0
        for matching_killmail in matching_killmails_qs:
            logger.debug(
                "Sending killmail with ID %d to webhook", matching_killmail.killmail.id
            )
            sleep(DISCORD_SEND_DELAY)
            if matching_killmail.send_to_webhook():
                killmail_counter += 1

        return killmail_counter


class TrackedKillmail(models.Model):
    """A tracked killmail
    
    Tracked killmails contains additional calculated information about the killmail 
    and processing information from the related tracker
    """

    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE)
    killmail = models.ForeignKey(Killmail, on_delete=models.CASCADE)
    is_matching = models.BooleanField(default=None, db_index=True, null=True)
    date_sent = models.DateTimeField(default=None, db_index=True, null=True)
    attackers_count = models.PositiveIntegerField(
        default=None,
        null=True,
        db_index=True,
        help_text="Calculated number of attackers",
    )
    jumps = models.PositiveIntegerField(
        default=None,
        null=True,
        db_index=True,
        help_text="Calculated number of jumps from origin",
    )
    distance = models.FloatField(
        default=None,
        null=True,
        db_index=True,
        help_text="Calculated distance from origin in lightyears",
    )
    solar_system = models.ForeignKey(
        EveSolarSystem,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
    )
    is_high_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_low_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_null_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_w_space = models.BooleanField(default=None, null=True, db_index=True)
    victim_ship_type = models.ForeignKey(
        EveType,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="trackedkillmail_victim_ship_types_set",
    )
    attackers_ship_types = models.ManyToManyField(
        EveType, default=None, related_name="trackedkillmail_attackers_ship_types_set"
    )

    objects = TrackedKillmailManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tracker", "killmail"], name="functional_key"
            )
        ]

    def __repr__(self):
        return (
            f"{type(self).__name__}(tracker='{self.tracker}'"
            f", killmail_id={self.killmail_id})"
        )

    def send_to_webhook(self) -> bool:
        killmail = self.killmail
        if killmail.victim.character:
            victim_str = (
                f"{self._entity_zkb_link(killmail.victim.character)} "
                f"({self._entity_zkb_link(killmail.victim.corporation)}) "
            )
            victim_name = killmail.victim.character.name
        else:
            victim_str = f"{self._entity_zkb_link(killmail.victim.corporation)}"
            victim_name = killmail.victim.corporation.name

        attacker = killmail.attackers.get(is_final_blow=True)
        if attacker.character and attacker.corporation:
            attacker_str = (
                f"{self._entity_zkb_link(attacker.character)} "
                f"({self._entity_zkb_link(attacker.corporation)})"
            )
        elif attacker.corporation:
            attacker_str = f"{self._entity_zkb_link(attacker.corporation)}"
        elif attacker.faction:
            attacker_str = f"**{attacker.faction.name}**"
        else:
            attacker_str = "(Unknown attacker)"

        value_mio = int(killmail.zkb.total_value / 1000000)
        try:
            victim_ship_type_name = killmail.victim.ship_type.name
        except AttributeError:
            victim_ship_type_name = ""

        try:
            attacker_ship_type_name = attacker.ship_type.name
        except AttributeError:
            attacker_ship_type_name = ""

        description = (
            f"{victim_str} lost their **{victim_ship_type_name}** "
            f"in {self._entity_dotlan_link(killmail.solar_system)} "
            f"worth **{value_mio} M** ISK.\n"
            f"Final blow by {attacker_str} "
            f"in a **{attacker_ship_type_name}**.\n"
            f"Attackers: {killmail.attackers.count()}"
        )
        if self.tracker.origin_solar_system:
            origin_solar_system_link = self._entity_dotlan_link(
                self.tracker.origin_solar_system
            )
            distance = f"{self.distance:,.2f}" if self.distance is not None else "?"
            jumps = self.jumps if self.jumps is not None else "?"
            description += (
                f"\nDistance from {origin_solar_system_link}: "
                f"{distance} LY | {jumps} jumps"
            )

        title = (
            f"{killmail.solar_system.name} | {victim_ship_type_name} | "
            f"{victim_name} | Killmail"
        )
        thumbnail_url = killmail.victim.ship_type.icon_url()
        zkb_killmail_url = f"{self.killmail.ZKB_KILLMAIL_BASEURL}{self.killmail_id}/"
        embed = dhooks_lite.Embed(
            description=description,
            title=title,
            url=zkb_killmail_url,
            thumbnail=dhooks_lite.Thumbnail(url=thumbnail_url),
            footer=dhooks_lite.Footer(
                text="zKillboard", icon_url=Webhook.zkb_icon_url()
            ),
            timestamp=killmail.time,
        )
        if self.tracker.ping_type == Tracker.PING_TYPE_EVERYBODY:
            intro = "@everybody "
        elif self.tracker.ping_type == Tracker.PING_TYPE_HERE:
            intro = "@here "
        else:
            intro = ""

        if self.tracker.is_posting_name:
            intro += f"Tracker **{self.tracker.name}**:"

        logger.info(
            "Tracker %s: Sending alert to Discord for killmail %s",
            self.tracker,
            self.killmail,
        )
        hook = dhooks_lite.Webhook(url=self.tracker.webhook.url)
        response = hook.execute(
            content=intro,
            embeds=[embed],
            username=Webhook.default_username(),
            avatar_url=Webhook.default_avatar_url(),
            wait_for_response=True,
        )
        logger.debug("headers: %s", response.headers)
        logger.debug("status_code: %s", response.status_code)
        logger.debug("content: %s", response.content)
        if response.status_ok:
            self.date_sent = now()
            self.save()
            return True
        else:
            logger.warning(
                "Failed to send message to Discord. HTTP status code: %d, response: %s",
                response.status_code,
                response.content,
            )
            return False

    @classmethod
    def _entity_zkb_link(cls, eve_obj: object) -> str:
        try:
            return cls._convert_to_discord_link(eve_obj.name, eve_obj.zkb_url)
        except AttributeError:
            return ""

    @classmethod
    def _entity_dotlan_link(cls, eve_obj: object) -> str:
        try:
            return cls._convert_to_discord_link(eve_obj.name, eve_obj.dotlan_url)
        except AttributeError:
            return ""

    @classmethod
    def _convert_to_discord_link(cls, name: str, url: str) -> str:
        return f"[{str(name)}]({str(url)})"
