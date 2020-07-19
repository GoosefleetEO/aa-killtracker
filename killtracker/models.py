from copy import deepcopy
from datetime import timedelta
import json
from time import sleep
from urllib.parse import urljoin

import dhooks_lite
from redismq import RedisMQ

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.evelinks import eveimageserver
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

from eveuniverse.helpers import meters_to_ly
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
from .helpers.killmails import KillmailTemp, TrackerInfo
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

    def load_entities(self):
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

    ZKB_KILLMAIL_BASEURL = "https://zkillboard.com/kill/"

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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._redis_mq = RedisMQ(
            cache.get_master_client(), f"{__title__}_webhook_{self.pk}"
        )

    def __str__(self):
        return self.name

    def __repr__(self):
        return "{}(id={}, name='{}')".format(
            self.__class__.__name__, self.id, self.name
        )

    def add_killmail_to_queue(self, killmail: KillmailTemp) -> int:
        """Adds killmail to queue for later sending
        
        Returns updated size of queue
        """
        return self._queue().enqueue(killmail.asjson())

    def send_queued_killmails(self) -> int:
        """sends all killmails in the queue to this webhook
        
        returns number of successfull sent messages
        """
        killmail_counter = 0
        queue = self._queue()
        while True:
            message = queue.dequeue()
            if message:
                killmail = KillmailTemp.from_json(message)
                logger.debug(
                    "Sending killmail with ID %d to webhook %s", killmail.id, self
                )
                sleep(DISCORD_SEND_DELAY)
                if self.send_killmail(killmail):
                    killmail_counter += 1

            else:
                break

        return killmail_counter

    def queue_size(self) -> int:
        return self._queue().size()

    def _queue(self) -> RedisMQ:
        """returns the queue object of this webhook"""
        return self._redis_mq

    def send_killmail(self, killmail: KillmailTemp) -> bool:
        EveEntity.objects.bulk_create_esi(ids=killmail.entity_ids())
        if killmail.victim.character_id:
            victim_str = (
                f"{self._entity_zkb_link(killmail.victim.character_id)} "
                f"({self._entity_zkb_link(killmail.victim.corporation_id)}) "
            )
            victim_name = EveEntity.objects.get_name(killmail.victim.character_id)
        else:
            victim_str = f"{self._entity_zkb_link(killmail.victim.corporation_id)}"
            victim_name = EveEntity.objects.get_name(killmail.victim.corporation.name)

        # final attacker
        final_attacker = None
        for attacker in killmail.attackers:
            if attacker.is_final_blow:
                final_attacker = attacker
                break

        if final_attacker:
            if final_attacker.character_id and final_attacker.corporation_id:
                final_attacker_str = (
                    f"{self._entity_zkb_link(final_attacker.character_id)} "
                    f"({self._entity_zkb_link(final_attacker.corporation_id)})"
                )
            elif final_attacker.corporation_id:
                final_attacker_str = (
                    f"{self._entity_zkb_link(final_attacker.corporation_id)}"
                )
            elif final_attacker.faction_id:
                final_attacker_str = (
                    f"**{EveEntity.objects.get_name(final_attacker.faction_id)}**"
                )
            else:
                final_attacker_str = "(Unknown final_attacker)"

            final_attacker_ship_type_name = EveEntity.objects.get_name(
                final_attacker.ship_type_id
            )

        else:
            final_attacker_str = ""
            final_attacker_ship_type_name = ""

        value_mio = int(killmail.zkb.total_value / 1000000)
        victim_ship_type_name = EveEntity.objects.get_name(killmail.victim.ship_type_id)

        description = (
            f"{victim_str} lost their **{victim_ship_type_name}** "
            f"in {self._entity_dotlan_link(killmail.solar_system_id)} "
            f"worth **{value_mio} M** ISK.\n"
            f"Final blow by {final_attacker_str} "
            f"in a **{final_attacker_ship_type_name}**.\n"
            f"Attackers: {len(killmail.attackers)}"
        )

        # tracker info
        if killmail.tracker_info:
            tracker = Tracker.objects.get(pk=killmail.tracker_info.tracker_pk)
            if tracker.origin_solar_system:
                origin_solar_system_link = self._convert_to_discord_link(
                    name=tracker.origin_solar_system.name,
                    url=tracker.origin_solar_system.dotlan_url,
                )
                if killmail.tracker_info.distance:
                    distance_str = f"{killmail.tracker_info.distance:,.2f}"
                else:
                    distance_str = "?"

                if killmail.tracker_info.jumps:
                    jumps_str = killmail.tracker_info.jumps
                else:
                    jumps_str = "?"

                description += (
                    f"\nDistance from {origin_solar_system_link}: "
                    f"{distance_str} LY | {jumps_str} jumps"
                )
        else:
            tracker = None

        solar_system_name = EveEntity.objects.get_name(killmail.solar_system_id)

        title = (
            f"{solar_system_name} | {victim_ship_type_name} | "
            f"{victim_name} | Killmail"
        )
        thumbnail_url = eveimageserver.type_icon_url(
            killmail.victim.ship_type_id, size=128
        )
        zkb_killmail_url = f"{self.ZKB_KILLMAIL_BASEURL}{killmail.id}/"
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
        if tracker:
            if tracker.ping_type == Tracker.PING_TYPE_EVERYBODY:
                intro = "@everybody "
            elif tracker.ping_type == Tracker.PING_TYPE_HERE:
                intro = "@here "
            else:
                intro = ""

            if tracker.is_posting_name:
                intro += f"Tracker **{tracker.name}**:"

        else:
            intro = ""

        logger.info(
            "%sSending killmail to Discord for killmail %s",
            f"Tracker {tracker.name}: " if tracker else "",
            killmail.id,
        )

        hook = dhooks_lite.Webhook(url=self.url)
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
    def _entity_zkb_link(cls, entity_id: int) -> str:
        eve_obj, _ = EveEntity.objects.get_or_create_esi(id=entity_id)
        try:
            return cls._convert_to_discord_link(name=eve_obj.name, url=eve_obj.zkb_url)
        except AttributeError:
            return ""

    @classmethod
    def _entity_dotlan_link(cls, entity_id: int) -> str:
        eve_obj, _ = EveEntity.objects.get_or_create_esi(id=entity_id)
        try:
            return cls._convert_to_discord_link(
                name=eve_obj.name, url=eve_obj.dotlan_url
            )
        except AttributeError:
            return ""

    @classmethod
    def _convert_to_discord_link(cls, name: str, url: str) -> str:
        return f"[{str(name)}]({str(url)})"

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

    def calculate_killmail(self, killmail: KillmailTemp) -> KillmailTemp:
        threshold_date = now() - timedelta(
            hours=KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
        )
        if killmail.time < threshold_date:
            return False

        # calculate missing information
        solar_system = None
        distance = None
        jumps = None
        is_high_sec = None
        is_low_sec = None
        is_null_sec = None
        is_w_space = None
        if killmail.solar_system_id:
            solar_system, _ = EveSolarSystem.objects.get_or_create_esi(
                id=killmail.solar_system_id
            )
            is_high_sec = solar_system.is_high_sec
            is_low_sec = solar_system.is_low_sec
            is_null_sec = solar_system.is_null_sec
            is_w_space = solar_system.is_w_space
            if self.origin_solar_system:
                distance = meters_to_ly(
                    self.origin_solar_system.distance_to(solar_system)
                )
                jumps = self.origin_solar_system.jumps_to(solar_system)

        victim_ship_type = None
        if killmail.victim and killmail.victim.ship_type_id:
            victim_ship_type, _ = EveType.objects.get_or_create_esi(
                id=killmail.victim.ship_type_id
            )

        attacker_ship_types = list()
        if len(killmail.attackers) > 0:
            for attacker in killmail.attackers:
                if attacker.ship_type_id:
                    attacker_ship_types.append(
                        EveType.objects.get_or_create_esi(id=attacker.ship_type_id)
                    )

        # apply filter
        is_matching = True

        try:
            if is_matching and self.exclude_high_sec:
                is_matching = not is_high_sec

            if is_matching and self.exclude_low_sec:
                is_matching = not is_low_sec

            if is_matching and self.exclude_null_sec:
                is_matching = not is_null_sec

            if is_matching and self.exclude_w_space:
                is_matching = not is_w_space

            if is_matching and self.require_min_attackers:
                is_matching = len(killmail.attackers) >= self.require_min_attackers

            if is_matching and self.require_max_attackers:
                is_matching = len(killmail.attackers) <= self.require_max_attackers

            if is_matching and self.exclude_npc_kills:
                is_matching = not killmail.zkb.is_npc

            if is_matching and self.require_npc_kills:
                is_matching = killmail.zkb.is_npc

            if is_matching and self.require_min_value:
                is_matching = killmail.zkb.total_value >= self.require_min_value

            if is_matching and self.require_max_distance:
                is_matching = distance is not None and (
                    distance <= self.require_max_distance
                )

            if is_matching and self.require_max_jumps:
                is_matching = jumps is not None and (jumps <= self.require_max_jumps)

            if is_matching and self.require_regions.count() > 0:
                is_matching = solar_system is not None and solar_system.eve_constellation.eve_region_id in self.require_regions.all().values_list(
                    "id", flat=True
                )

            if is_matching and self.require_constellations.count() > 0:
                is_matching = solar_system is not None and solar_system.eve_constellation_id in self.require_constellations.all().values_list(
                    "id", flat=True
                )

            if is_matching and self.require_solar_systems.count() > 0:
                is_matching = solar_system is not None and solar_system.id in self.require_solar_systems.all().values_list(
                    "id", flat=True
                )

            attacker_alliance_ids = {
                attacker.alliance_id for attacker in killmail.attackers
            }
            if is_matching and self.exclude_attacker_alliances.count() > 0:
                excluded_alliance_ids = set(
                    self._extract_alliance_ids(self.exclude_attacker_alliances)
                )
                is_matching = (
                    attacker_alliance_ids.intersection(excluded_alliance_ids) == set()
                )

            if is_matching and self.require_attacker_alliances.count() > 0:
                required_alliance_ids = set(
                    self._extract_alliance_ids(self.require_attacker_alliances)
                )
                is_matching = (
                    attacker_alliance_ids.difference(required_alliance_ids) == set()
                )

            attacker_corporation_ids = {
                attacker.corporation_id for attacker in killmail.attackers
            }
            if is_matching and self.exclude_attacker_corporations.count() > 0:
                excluded_corporation_ids = set(
                    self._extract_corporation_ids(self.exclude_attacker_corporations)
                )
                is_matching = (
                    len(attacker_corporation_ids.intersection(excluded_corporation_ids))
                    == 0
                )

            if is_matching and self.require_attacker_corporations.count() > 0:
                required_corporation_ids = set(
                    self._extract_corporation_ids(self.require_attacker_corporations)
                )
                is_matching = (
                    len(attacker_corporation_ids.intersection(required_corporation_ids))
                    > 0
                )

            if is_matching and self.require_victim_alliances.count() > 0:
                required_alliance_ids = set(
                    self._extract_alliance_ids(self.require_victim_alliances)
                )
                is_matching = killmail.victim.alliance_id in required_alliance_ids

            if is_matching and self.require_victim_corporations.count() > 0:
                required_corporation_ids = set(
                    self._extract_corporation_ids(self.require_victim_corporations)
                )
                is_matching = killmail.victim.corporation_id in required_corporation_ids

            if is_matching and self.require_victim_ship_groups.count() > 0:
                required_ship_group_ids = set(
                    self.require_victim_ship_groups.values_list("id", flat=True)
                )
                is_matching = victim_ship_type.eve_group_id in required_ship_group_ids

            if is_matching and self.require_attackers_ship_groups.count() > 0:
                attacker_ship_group_ids = set()
                for attacker in killmail.attackers:
                    obj, _ = EveType.objects.get_or_create_esi(id=attacker.ship_type_id)
                    attacker_ship_group_ids.add(obj.eve_group_id)

                required_ship_group_ids = set(
                    self.require_attackers_ship_groups.values_list("id", flat=True)
                )
                is_matching = (
                    len(attacker_ship_group_ids.intersection(required_ship_group_ids))
                    > 0
                )

            if is_matching and self.require_attackers_ship_types.count() > 0:
                attacker_ship_type_ids = {
                    attacker.ship_type_id for attacker in killmail.attackers
                }
                required_ship_type_ids = set(
                    self.require_attackers_ship_types.values_list("id", flat=True)
                )
                is_matching = (
                    len(attacker_ship_type_ids.intersection(required_ship_type_ids)) > 0
                )

        except AttributeError:
            is_matching = False

        if is_matching:
            killmail_new = deepcopy(killmail)
            killmail_new.tracker_info = TrackerInfo(
                tracker_pk=self.pk, jumps=jumps, distance=distance
            )
            return killmail_new
        else:
            return None


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
