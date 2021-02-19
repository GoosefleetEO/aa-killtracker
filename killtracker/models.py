from copy import deepcopy
from datetime import timedelta
import json
from typing import Optional, List
from urllib.parse import urljoin

import dhooks_lite
from requests.exceptions import HTTPError
from simple_mq import SimpleMQ

from django.core.cache import cache
from django.contrib.auth.models import Group
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.evelinks import eveimageserver, zkillboard, dotlan
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.modules.discord.models import DiscordUser

from app_utils.django import app_labels
from app_utils.logging import LoggerAddTag
from app_utils.json import JSONDateTimeEncoder, JSONDateTimeDecoder
from app_utils.urls import site_absolute_url
from app_utils.views import humanize_value
from eveuniverse.helpers import meters_to_ly, EveEntityNameResolver
from eveuniverse.models import (
    EveConstellation,
    EveRegion,
    EveSolarSystem,
    EveGroup,
    EveEntity,
    EveType,
)

from . import __title__, APP_NAME, HOMEPAGE_URL, __version__
from .app_settings import (
    KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER,
    KILLTRACKER_WEBHOOK_SET_AVATAR,
)
from .core.killmails import EntityCount, Killmail, TrackerInfo, ZKB_KILLMAIL_BASEURL
from .exceptions import WebhookTooManyRequests
from .managers import EveKillmailManager, TrackerManager, WebhookManager


logger = LoggerAddTag(get_extension_logger(__name__), __title__)


class EveKillmail(models.Model):

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    solar_system = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = EveKillmailManager()

    def __str__(self):
        return f"ID:{self.id}"

    def __repr__(self):
        return f"{type(self).__name__}(id={self.id})"

    def load_entities(self):
        """loads unknown entities for this killmail"""
        qs = EveEntity.objects.filter(id__in=self.entity_ids(), name="")
        qs.update_from_esi()

    def entity_ids(self) -> List[int]:
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
        return [int(x) for x in ids if x is not None]


class EveKillmailCharacter(models.Model):

    character = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )
    corporation = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )
    alliance = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )
    faction = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )
    ship_type = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True

    def __str__(self) -> str:
        if self.character:
            return str(self.character)
        elif self.corporation:
            return str(self.corporation)
        elif self.alliance:
            return str(self.alliance)
        elif self.faction:
            return str(self.faction)
        else:
            return f"PK:{self.pk}"


class EveKillmailVictim(EveKillmailCharacter):

    killmail = models.OneToOneField(
        EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="victim"
    )
    damage_taken = models.BigIntegerField(default=None, null=True, blank=True)


class EveKillmailAttacker(EveKillmailCharacter):

    killmail = models.ForeignKey(
        EveKillmail, on_delete=models.CASCADE, related_name="attackers"
    )
    damage_done = models.BigIntegerField(default=None, null=True, blank=True)
    is_final_blow = models.BooleanField(
        default=None, null=True, blank=True, db_index=True
    )
    security_status = models.FloatField(default=None, null=True, blank=True)
    weapon_type = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="+",
    )


class EveKillmailPosition(models.Model):
    killmail = models.OneToOneField(
        EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="position"
    )
    x = models.FloatField(default=None, null=True, blank=True)
    y = models.FloatField(default=None, null=True, blank=True)
    z = models.FloatField(default=None, null=True, blank=True)


class EveKillmailZkb(models.Model):

    killmail = models.OneToOneField(
        EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="zkb"
    )
    location_id = models.PositiveIntegerField(
        default=None, null=True, blank=True, db_index=True
    )
    hash = models.CharField(max_length=64, default="", blank=True)
    fitted_value = models.FloatField(default=None, null=True, blank=True)
    total_value = models.FloatField(default=None, null=True, blank=True, db_index=True)
    points = models.PositiveIntegerField(
        default=None, null=True, blank=True, db_index=True
    )
    is_npc = models.BooleanField(default=None, null=True, blank=True, db_index=True)
    is_solo = models.BooleanField(default=None, null=True, blank=True, db_index=True)
    is_awox = models.BooleanField(default=None, null=True, blank=True, db_index=True)


class Webhook(models.Model):
    """A webhook to receive messages"""

    HTTP_TOO_MANY_REQUESTS = 429

    class WebhookType(models.IntegerChoices):
        DISCORD = 1, _("Discord Webhook")

    name = models.CharField(
        max_length=64, unique=True, help_text="short name to identify this webhook"
    )
    webhook_type = models.IntegerField(
        choices=WebhookType.choices,
        default=WebhookType.DISCORD,
        help_text="type of this webhook",
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
        blank=True,
        help_text="you can add notes about this webhook here if you want",
    )
    is_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text="whether notifications are currently sent to this webhook",
    )
    objects = WebhookManager()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.main_queue = self._create_queue("main")
        self.error_queue = self._create_queue("error")

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return "{}(id={}, name='{}')".format(
            self.__class__.__name__, self.id, self.name
        )

    def __getstate__(self):
        # Copy the object's state from self.__dict__ which contains
        # all our instance attributes. Always use the dict.copy()
        # method to avoid modifying the original state.
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["main_queue"]
        del state["error_queue"]
        return state

    def __setstate__(self, state):
        # Restore instance attributes (i.e., filename and lineno).
        self.__dict__.update(state)
        # Restore the previously opened file's state. To do so, we need to
        # reopen it and read from it until the line count is restored.
        self.main_queue = self._create_queue("main")
        self.error_queue = self._create_queue("error")

    def save(self, *args, **kwargs):
        is_new = self.id is None
        super().save(*args, **kwargs)
        if is_new:
            self.main_queue = self._create_queue("main")
            self.error_queue = self._create_queue("error")

    def _create_queue(self, suffix: str) -> Optional[SimpleMQ]:
        return (
            SimpleMQ(
                cache.get_master_client(), f"{__title__}_webhook_{self.pk}_{suffix}"
            )
            if self.pk
            else None
        )

    def reset_failed_messages(self) -> int:
        """moves all messages from error queue into main queue.
        returns number of moved messages.
        """
        counter = 0
        while True:
            message = self.error_queue.dequeue()
            if message is None:
                break
            else:
                self.main_queue.enqueue(message)
                counter += 1

        return counter

    def enqueue_message(
        self,
        content: str = None,
        embeds: List[dhooks_lite.Embed] = None,
        tts: bool = None,
        username: str = None,
        avatar_url: str = None,
    ) -> int:
        """Enqueues a message to be send with this webhook"""
        username = __title__ if KILLTRACKER_WEBHOOK_SET_AVATAR else username
        brand_url = urljoin(
            site_absolute_url(),
            staticfiles_storage.url("killtracker/killtracker_logo.png"),
        )
        avatar_url = brand_url if KILLTRACKER_WEBHOOK_SET_AVATAR else avatar_url
        return self.main_queue.enqueue(
            self._discord_message_asjson(
                content=content,
                embeds=embeds,
                tts=tts,
                username=username,
                avatar_url=avatar_url,
            )
        )

    @staticmethod
    def _discord_message_asjson(
        content: str = None,
        embeds: List[dhooks_lite.Embed] = None,
        tts: bool = None,
        username: str = None,
        avatar_url: str = None,
    ) -> str:
        """Converts a Discord message to JSON and returns it

        Raises ValueError if mesage is incomplete
        """
        if not content and not embeds:
            raise ValueError("Message must have content or embeds to be valid")

        if embeds:
            embeds_list = [obj.asdict() for obj in embeds]
        else:
            embeds_list = None

        message = dict()
        if content:
            message["content"] = content
        if embeds_list:
            message["embeds"] = embeds_list
        if tts:
            message["tts"] = tts
        if username:
            message["username"] = username
        if avatar_url:
            message["avatar_url"] = avatar_url

        return json.dumps(message, cls=JSONDateTimeEncoder)

    def send_message_to_webhook(self, message_json: str) -> bool:
        """Send given message to webhook

        Params
            message_json: Discord message encoded in JSON
        """
        timeout = cache.ttl(self._blocked_cache_key())
        if timeout:
            raise WebhookTooManyRequests(timeout)

        message = json.loads(message_json, cls=JSONDateTimeDecoder)
        if message.get("embeds"):
            embeds = [
                dhooks_lite.Embed.from_dict(embed_dict)
                for embed_dict in message.get("embeds")
            ]
        else:
            embeds = None
        hook = dhooks_lite.Webhook(
            url=self.url,
            user_agent=dhooks_lite.UserAgent(
                name=APP_NAME, url=HOMEPAGE_URL, version=__version__
            ),
        )
        response = hook.execute(
            content=message.get("content"),
            embeds=embeds,
            username=message.get("username"),
            avatar_url=message.get("avatar_url"),
            wait_for_response=True,
            max_retries=0,  # we will handle retries ourselves
        )
        logger.debug("headers: %s", response.headers)
        logger.debug("status_code: %s", response.status_code)
        logger.debug("content: %s", response.content)
        if response.status_code == self.HTTP_TOO_MANY_REQUESTS:
            logger.error(
                "%s: Received too many requests error from API: %s",
                self,
                response.content,
            )
            try:
                retry_after = int(response.content.get("retry_after")) + 2
            except (ValueError, TypeError):
                retry_after = WebhookTooManyRequests.DEFAULT_RESET_AFTER
            cache.set(
                key=self._blocked_cache_key(), value="BLOCKED", timeout=retry_after
            )
            raise WebhookTooManyRequests(retry_after)

        return response

    def _blocked_cache_key(self) -> str:
        return f"{__title__}_webhook_{self.pk}_blocked"

    @staticmethod
    def create_message_link(name: str, url: str) -> str:
        """Create link for a Discord message"""
        return f"[{str(name)}]({str(url)})"


class Tracker(models.Model):

    ICON_SIZE = 128
    MAIN_MINIMUM_COUNT = 2
    MAIN_MINIMUM_SHARE = 0.25

    class ChannelPingType(models.TextChoices):
        NONE = "PN", "(none)"
        HERE = "PH", "@here"
        EVERYBODY = "PE", "@everybody"

    name = models.CharField(
        max_length=100,
        help_text="name to identify tracker. Will be shown on alerts posts.",
        unique=True,
    )
    description = models.TextField(
        blank=True,
        help_text=(
            "Brief description what this tracker is for. Will not be shown on alerts."
        ),
    )
    color = models.CharField(
        max_length=7,
        default="",
        blank=True,
        help_text=(
            "Optional color for embed on Discord - #000000 / "
            "black means no color selected"
        ),
    )
    origin_solar_system = models.ForeignKey(
        EveSolarSystem,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        related_name="+",
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
        related_name="+",
        default=None,
        blank=True,
        help_text="exclude killmails with attackers from one of these alliances",
    )
    require_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="only include killmails with attackers from one of these alliances",
    )
    exclude_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="exclude killmails with attackers from one of these corporations",
    )
    require_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="only include killmails with attackers from one of these corporations",
    )
    require_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "only include killmails where the victim belongs to one of these alliances"
        ),
    )
    require_victim_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
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
        default=False,
        help_text=("exclude killmails with blue attackers"),
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
        related_name="+",
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_constellations = models.ManyToManyField(
        EveConstellation,
        default=None,
        blank=True,
        related_name="+",
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_solar_systems = models.ManyToManyField(
        EveSolarSystem,
        default=None,
        blank=True,
        related_name="+",
        help_text=("Only include killmails that occurred in one of these regions"),
    )
    require_min_value = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmail's value to be greater or equal to the given value in M ISK",
    )
    require_attackers_ship_groups = models.ManyToManyField(
        EveGroup,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship groups"
        ),
    )
    require_attackers_ship_types = models.ManyToManyField(
        EveType,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship types"
        ),
    )
    require_victim_ship_groups = models.ManyToManyField(
        EveGroup,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where victim is flying one of these ship groups"
        ),
    )
    require_victim_ship_types = models.ManyToManyField(
        EveType,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where victim is flying one of these ship types"
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
        choices=ChannelPingType.choices,
        default=ChannelPingType.NONE,
        verbose_name="channel pings",
        help_text="Option to ping every member of the channel",
    )
    ping_groups = models.ManyToManyField(
        Group,
        default=None,
        blank=True,
        verbose_name="group pings",
        related_name="+",
        help_text="Option to ping specific group members - ",
    )
    is_posting_name = models.BooleanField(
        default=True, help_text="whether posted messages include the tracker's name"
    )
    is_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text="toogle for activating or deactivating a tracker",
    )

    objects = TrackerManager()

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.color == "#000000":
            self.color = ""
        super().save(*args, **kwargs)

    @property
    def has_localization_clause(self) -> bool:
        """returns True if tracker has a clause that needs the killmais's solar system"""
        return (
            self.exclude_high_sec
            or self.exclude_low_sec
            or self.exclude_null_sec
            or self.exclude_w_space
            or self.require_max_distance is not None
            or self.require_max_jumps is not None
            or self.require_regions.all()
            or self.require_constellations.all()
            or self.require_solar_systems.all()
        )

    @property
    def has_type_clause(self) -> bool:
        """returns True if tracker has a clause that needs a type from the killmail,
        e.g. the ship type of the victim
        """
        return (
            self.require_attackers_ship_groups.all()
            or self.require_attackers_ship_types.all()
            or self.require_victim_ship_groups.all()
            or self.require_victim_ship_types.all()
        )

    def process_killmail(
        self, killmail: Killmail, ignore_max_age: bool = False
    ) -> Optional[Killmail]:
        """runs tracker on given killmail

        returns new killmail amended with tracker info if killmail matches
        else returns None
        """
        threshold_date = now() - timedelta(
            minutes=KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
        )
        if not ignore_max_age and killmail.time < threshold_date:
            return False

        # pre-calculate shared information
        solar_system = None
        distance = None
        jumps = None
        is_high_sec = None
        is_low_sec = None
        is_null_sec = None
        is_w_space = None
        matching_ship_type_ids = None
        if killmail.solar_system_id and (
            self.origin_solar_system or self.has_localization_clause
        ):
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

        # Make sure all ship types are in the local database
        if self.has_type_clause:
            EveType.objects.bulk_get_or_create_esi(ids=killmail.ship_type_ids())

        # apply filters
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
                is_matching = (
                    killmail.zkb.total_value >= self.require_min_value * 1000000
                )

            if is_matching and self.require_max_distance:
                is_matching = distance is not None and (
                    distance <= self.require_max_distance
                )

            if is_matching and self.require_max_jumps:
                is_matching = jumps is not None and (jumps <= self.require_max_jumps)

            if is_matching and self.require_regions.exists():
                is_matching = (
                    solar_system
                    and self.require_regions.filter(
                        id=solar_system.eve_constellation.eve_region_id
                    ).exists()
                )

            if is_matching and self.require_constellations.exists():
                is_matching = (
                    solar_system
                    and self.require_constellations.filter(
                        id=solar_system.eve_constellation_id
                    ).exists()
                )

            if is_matching and self.require_solar_systems.exists():
                is_matching = (
                    solar_system
                    and self.require_solar_systems.filter(id=solar_system.id).exists()
                )

            if is_matching and self.exclude_attacker_alliances.exists():
                is_matching = self.exclude_attacker_alliances.exclude(
                    alliance_id__in=killmail.attackers_alliance_ids()
                ).exists()

            if is_matching and self.require_attacker_alliances.exists():
                is_matching = self.require_attacker_alliances.filter(
                    alliance_id__in=killmail.attackers_alliance_ids()
                ).exists()

            if is_matching and self.exclude_attacker_corporations.exists():
                is_matching = self.exclude_attacker_corporations.exclude(
                    corporation_id__in=killmail.attackers_corporation_ids()
                ).exists()

            if is_matching and self.require_attacker_corporations.exists():
                is_matching = self.require_attacker_corporations.filter(
                    corporation_id__in=killmail.attackers_corporation_ids()
                ).exists()

            if is_matching and self.require_victim_alliances.exists():
                is_matching = self.require_victim_alliances.filter(
                    alliance_id=killmail.victim.alliance_id
                ).exists()

            if is_matching and self.require_victim_corporations.exists():
                is_matching = self.require_victim_corporations.filter(
                    corporation_id=killmail.victim.corporation_id
                ).exists()

            if is_matching and self.require_victim_ship_groups.exists():
                ship_types_matching_qs = EveType.objects.filter(
                    eve_group_id__in=list(
                        self.require_victim_ship_groups.values_list("id", flat=True)
                    ),
                    id=killmail.victim.ship_type_id,
                )
                is_matching = ship_types_matching_qs.exists()
                if is_matching:
                    matching_ship_type_ids = list(
                        ship_types_matching_qs.values_list("id", flat=True)
                    )

            if is_matching and self.require_victim_ship_types.exists():
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=list(
                        self.require_victim_ship_types.values_list("id", flat=True)
                    ),
                    id=killmail.victim.ship_type_id,
                )
                is_matching = ship_types_matching_qs.exists()
                if is_matching:
                    matching_ship_type_ids = list(
                        ship_types_matching_qs.values_list("id", flat=True)
                    )

            if is_matching and self.require_attackers_ship_groups.exists():
                attackers_ship_type_ids = killmail.attackers_ship_type_ids()
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=attackers_ship_type_ids
                ).filter(
                    eve_group_id__in=list(
                        self.require_attackers_ship_groups.values_list("id", flat=True)
                    )
                )
                is_matching = ship_types_matching_qs.exists()
                if is_matching:
                    matching_ship_type_ids = list(
                        ship_types_matching_qs.values_list("id", flat=True)
                    )

            if is_matching and self.require_attackers_ship_types.exists():
                attackers_ship_type_ids = killmail.attackers_ship_type_ids()
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=attackers_ship_type_ids
                ).filter(
                    id__in=list(
                        self.require_attackers_ship_types.values_list("id", flat=True)
                    )
                )
                is_matching = ship_types_matching_qs.exists()
                if is_matching:
                    matching_ship_type_ids = list(
                        ship_types_matching_qs.values_list("id", flat=True)
                    )

        except AttributeError:
            is_matching = False

        if is_matching:
            killmail_new = deepcopy(killmail)
            killmail_new.tracker_info = TrackerInfo(
                tracker_pk=self.pk,
                jumps=jumps,
                distance=distance,
                main_org=self._killmail_main_attacker_org(killmail),
                main_ship_group=self._killmail_main_attacker_ship_group(killmail),
                matching_ship_type_ids=matching_ship_type_ids,
            )
            return killmail_new
        else:
            return None

    @classmethod
    def _killmail_main_attacker_org(cls, killmail) -> Optional[EntityCount]:
        """returns the main attacker group with count"""
        org_items = []
        for attacker in killmail.attackers:
            if attacker.alliance_id:
                org_items.append(
                    EntityCount(
                        id=attacker.alliance_id, category=EntityCount.CATEGORY_ALLIANCE
                    )
                )

            if attacker.corporation_id:
                org_items.append(
                    EntityCount(
                        id=attacker.corporation_id,
                        category=EntityCount.CATEGORY_CORPORATION,
                    )
                )

        if org_items:
            org_items_2 = [
                EntityCount(id=x.id, category=x.category, count=org_items.count(x))
                for x in set(org_items)
            ]
            max_count = max([x.count for x in org_items_2])
            treshold = max(
                len(killmail.attackers) * cls.MAIN_MINIMUM_SHARE,
                cls.MAIN_MINIMUM_COUNT,
            )
            if max_count >= treshold:
                org_items_3 = [x for x in org_items_2 if x.count == max_count]
                if len(org_items_3) > 1:
                    org_items_4 = [x for x in org_items_3 if x.is_alliance]
                    if len(org_items_4) > 0:
                        return org_items_4[0]

                return org_items_3[0]

        return None

    @classmethod
    def _killmail_main_attacker_ship_group(
        cls, killmail: Killmail
    ) -> Optional[EntityCount]:
        """returns the main attacker group with count"""

        ships_type_ids = killmail.attackers_ship_type_ids()
        ship_types = EveType.objects.filter(id__in=ships_type_ids).select_related(
            "eve_group"
        )
        ship_groups = list()
        for ships_type_id in ships_type_ids:
            try:
                ship_type = ship_types.get(id=ships_type_id)
            except EveType.DoesNotExist:
                continue

            ship_groups.append(
                EntityCount(
                    id=ship_type.eve_group_id,
                    category=EntityCount.CATEGORY_INVENTORY_GROUP,
                    name=ship_type.eve_group.name,
                )
            )

        if ship_groups:
            ship_groups_2 = [
                EntityCount(
                    id=x.id,
                    category=x.category,
                    name=x.name,
                    count=ship_groups.count(x),
                )
                for x in set(ship_groups)
            ]
            max_count = max([x.count for x in ship_groups_2])
            treshold = max(
                len(killmail.attackers) * cls.MAIN_MINIMUM_SHARE,
                cls.MAIN_MINIMUM_COUNT,
            )
            if max_count >= treshold:
                return sorted(ship_groups_2, key=lambda x: x.count).pop()

        return None

    def generate_killmail_message(
        self, killmail: Killmail, intro_text: str = None
    ) -> int:
        """generate a message from given killmail and enqueue for later sending

        returns new queue size
        """
        embed = self._create_embed(killmail)
        content = self._create_content(intro_text)
        return self.webhook.enqueue_message(content=content, embeds=[embed])

    def _create_embed(self, killmail: Killmail) -> dhooks_lite.Embed:
        resolver = EveEntity.objects.bulk_resolve_names(ids=killmail.entity_ids())

        # victim
        if killmail.victim.alliance_id:
            victim_organization = EveEntity.objects.get(id=killmail.victim.alliance_id)
            victim_org_url = zkillboard.alliance_url(killmail.victim.alliance_id)
        elif killmail.victim.corporation_id:
            victim_organization = EveEntity.objects.get(
                id=killmail.victim.corporation_id
            )
            victim_org_url = zkillboard.corporation_url(killmail.victim.corporation_id)
        else:
            victim_organization = None
            victim_org_url = None

        if killmail.victim.corporation_id:
            victim_corporation_zkb_link = self._corporation_zkb_link(
                killmail.victim.corporation_id, resolver
            )
        else:
            victim_corporation_zkb_link = ""

        if killmail.victim.character_id:
            victim_character_zkb_link = self._character_zkb_link(
                killmail.victim.character_id,
                resolver,
            )
            victim_str = f"{victim_character_zkb_link} ({victim_corporation_zkb_link}) "
        elif killmail.victim.corporation_id:
            victim_str = victim_corporation_zkb_link
        else:
            victim_str = ""

        # final attacker
        for attacker in killmail.attackers:
            if attacker.is_final_blow:
                final_attacker = attacker
                break
        else:
            final_attacker = None

        if final_attacker:
            if final_attacker.corporation_id:
                final_attacker_corporation_zkb_link = self._corporation_zkb_link(
                    final_attacker.corporation_id, resolver
                )
            else:
                final_attacker_corporation_zkb_link = ""

            if final_attacker.character_id and final_attacker.corporation_id:
                final_attacker_character_zkb_link = self._character_zkb_link(
                    final_attacker.character_id, resolver
                )
                final_attacker_str = (
                    f"{final_attacker_character_zkb_link} "
                    f"({final_attacker_corporation_zkb_link})"
                )
            elif final_attacker.corporation_id:
                final_attacker_str = f"{final_attacker_corporation_zkb_link}"
            elif final_attacker.faction_id:
                final_attacker_str = (
                    f"**{resolver.to_name(final_attacker.faction_id)}**"
                )
            else:
                final_attacker_str = "(Unknown final_attacker)"

            final_attacker_ship_type_name = resolver.to_name(
                final_attacker.ship_type_id
            )

        else:
            final_attacker_str = ""
            final_attacker_ship_type_name = ""

        if killmail.solar_system_id:
            solar_system, _ = EveSolarSystem.objects.get_or_create_esi(
                id=killmail.solar_system_id
            )
            solar_system_link = self.webhook.create_message_link(
                name=solar_system.name, url=dotlan.solar_system_url(solar_system.name)
            )
            region_name = solar_system.eve_constellation.eve_region.name
            solar_system_text = f"{solar_system_link} ({region_name})"
        else:
            solar_system_text = ""

        # self info
        show_as_fleetkill = False
        distance_text = ""
        main_org_text = ""
        main_org_name = ""
        main_org_icon_url = eveimageserver.alliance_logo_url(1, size=self.ICON_SIZE)
        main_ship_group_text = ""
        tracked_ship_types_text = ""
        embed_color = None
        if killmail.tracker_info:
            show_as_fleetkill = self.identify_fleets
            embed_color = int(self.color[1:], 16) if self.color else None
            if self.origin_solar_system:
                origin_solar_system_link = self.webhook.create_message_link(
                    name=self.origin_solar_system.name,
                    url=dotlan.solar_system_url(self.origin_solar_system.name),
                )
                if killmail.tracker_info.distance is not None:
                    distance_str = f"{killmail.tracker_info.distance:,.1f}"
                else:
                    distance_str = "?"

                if killmail.tracker_info.jumps is not None:
                    jumps_str = killmail.tracker_info.jumps
                else:
                    jumps_str = "?"

                distance_text = (
                    f"\nDistance from {origin_solar_system_link}: "
                    f"{distance_str} LY | {jumps_str} jumps"
                )

            # main group
            main_org = killmail.tracker_info.main_org
            if main_org:
                main_org_name = resolver.to_name(main_org.id)
                if main_org.is_corporation:
                    main_org_link = self._corporation_zkb_link(main_org.id, resolver)
                    main_org_icon_url = eveimageserver.corporation_logo_url(
                        main_org.id, size=self.ICON_SIZE
                    )
                else:
                    main_org_link = self._alliance_zkb_link(main_org.id, resolver)
                    main_org_icon_url = eveimageserver.alliance_logo_url(
                        main_org.id, size=self.ICON_SIZE
                    )

                main_org_text = f" | Main group: {main_org_link} ({main_org.count})"

            else:
                show_as_fleetkill = False

            # main ship group
            main_ship_group = killmail.tracker_info.main_ship_group
            if main_ship_group:
                main_ship_group_text = f"\nMain ship class: **{main_ship_group.name}**"

            # tracked attacker ships
            matching_ship_type_ids = killmail.tracker_info.matching_ship_type_ids
            if matching_ship_type_ids:
                ship_types_text = "**, **".join(
                    sorted(
                        [
                            resolver.to_name(type_id)
                            for type_id in matching_ship_type_ids
                        ]
                    )
                )
                tracked_ship_types_text = (
                    f"\nTracked ship types involved: **{ship_types_text}**"
                )

        victim_ship_type_name = resolver.to_name(killmail.victim.ship_type_id)

        description = (
            f"{victim_str} lost their **{victim_ship_type_name}** "
            f"in {solar_system_text} "
            f"worth **{humanize_value(killmail.zkb.total_value)}** ISK.\n"
            f"Final blow by {final_attacker_str} "
            f"in a **{final_attacker_ship_type_name}**.\n"
            f"Attackers: **{len(killmail.attackers):,}**{main_org_text}"
            f"{main_ship_group_text}"
            f"{tracked_ship_types_text}"
            f"{distance_text}"
        )

        solar_system_name = resolver.to_name(killmail.solar_system_id)
        if show_as_fleetkill:
            title = f"{solar_system_name} | {main_org_name} | Fleetkill"
        else:
            title = f"{solar_system_name} | {victim_ship_type_name} | Killmail"

        if show_as_fleetkill:
            thumbnail_url = main_org_icon_url
        else:
            thumbnail_url = eveimageserver.type_icon_url(
                killmail.victim.ship_type_id, size=self.ICON_SIZE
            )

        zkb_killmail_url = f"{ZKB_KILLMAIL_BASEURL}{killmail.id}/"
        # TODO This is a workaround for Embed.Author.name. Address in dhooks_lite
        author = (
            dhooks_lite.Author(
                name=victim_organization.name if victim_organization.name else "?",
                url=victim_org_url,
                icon_url=victim_organization.icon_url(),
            )
            if victim_organization and victim_org_url
            else None
        )
        zkb_icon_url = urljoin(
            site_absolute_url(), staticfiles_storage.url("killtracker/zkb_icon.png")
        )
        embed = dhooks_lite.Embed(
            author=author,
            description=description,
            title=title,
            url=zkb_killmail_url,
            thumbnail=dhooks_lite.Thumbnail(url=thumbnail_url),
            footer=dhooks_lite.Footer(text="zKillboard", icon_url=zkb_icon_url),
            timestamp=killmail.time,
            color=embed_color,
        )
        return embed

    def _create_content(self, intro_text) -> str:
        intro_parts = []

        if self.ping_type == Tracker.ChannelPingType.EVERYBODY:
            intro_parts.append("@everybody")
        elif self.ping_type == Tracker.ChannelPingType.HERE:
            intro_parts.append("@here")

        if self.ping_groups.exists():
            if "discord" in app_labels():
                for group in self.ping_groups.all():
                    try:
                        role = DiscordUser.objects.group_to_role(group)
                    except HTTPError:
                        logger.warning(
                            "Failed to get Discord roles. Can not ping groups.",
                            exc_info=True,
                        )
                    else:
                        if role:
                            intro_parts.append(f"<@&{role['id']}>")

            else:
                logger.warning(
                    "Discord service needs to be installed in order "
                    "to use groups ping features."
                )

        if self.is_posting_name:
            intro_parts.append(f"Tracker **{self.name}**:")

        intro_parts_2 = []
        if intro_text:
            intro_parts_2.append(intro_text)
        if intro_parts:
            intro_parts_2.append(" ".join(intro_parts))

        return "\n".join(intro_parts_2)

    def _character_zkb_link(
        self, entity_id: int, resolver: EveEntityNameResolver
    ) -> str:
        return self.webhook.create_message_link(
            name=resolver.to_name(entity_id), url=zkillboard.character_url(entity_id)
        )

    def _corporation_zkb_link(
        self, entity_id: int, resolver: EveEntityNameResolver
    ) -> str:
        return self.webhook.create_message_link(
            name=resolver.to_name(entity_id), url=zkillboard.corporation_url(entity_id)
        )

    def _alliance_zkb_link(
        self, entity_id: int, resolver: EveEntityNameResolver
    ) -> str:
        return self.webhook.create_message_link(
            name=resolver.to_name(entity_id), url=zkillboard.alliance_url(entity_id)
        )
