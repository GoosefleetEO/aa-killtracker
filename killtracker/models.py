import json
from copy import deepcopy
from datetime import timedelta
from typing import List, Optional, Set

import dhooks_lite
from simple_mq import SimpleMQ

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from eveuniverse.helpers import meters_to_ly
from eveuniverse.models import (
    EveConstellation,
    EveEntity,
    EveGroup,
    EveRegion,
    EveSolarSystem,
    EveType,
)

from allianceauth.authentication.models import State
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger
from app_utils.allianceauth import get_redis_client
from app_utils.json import JSONDateTimeDecoder, JSONDateTimeEncoder
from app_utils.logging import LoggerAddTag
from app_utils.urls import static_file_absolute_url

from . import APP_NAME, HOMEPAGE_URL, __title__, __version__
from .app_settings import (
    KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER,
    KILLTRACKER_WEBHOOK_SET_AVATAR,
)
from .core.killmails import EntityCount, Killmail, TrackerInfo
from .exceptions import WebhookTooManyRequests
from .managers import (
    EveKillmailManager,
    EveTypePlusManager,
    TrackerManager,
    WebhookManager,
)

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


class EveTypePlus(EveType):
    """Variant to show group names with default output."""

    class Meta:
        proxy = True

    objects = EveTypePlusManager()

    def __str__(self) -> str:
        return f"{self.name} ({self.eve_group})"


class _EveKillmailCharacter(models.Model):
    """A character in a killmail for Eve Online. Can be both vitim and attacker."""

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

    def entity_ids(self) -> Set[int]:
        """IDs of all entity objects."""
        ids = {
            self.character_id,
            self.corporation_id,
            self.alliance_id,
            self.faction_id,
            self.ship_type_id,
        }
        ids.discard(None)
        return ids


class EveKillmail(_EveKillmailCharacter):
    """A killmail in Eve Online."""

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    solar_system = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)
    damage_taken = models.BigIntegerField(default=None, null=True, blank=True)
    # position
    position_x = models.FloatField(default=None, null=True, blank=True)
    position_y = models.FloatField(default=None, null=True, blank=True)
    position_z = models.FloatField(default=None, null=True, blank=True)
    # zkb
    location_id = models.PositiveIntegerField(
        default=None, null=True, blank=True, db_index=True
    )
    hash = models.CharField(max_length=64, default="", blank=True)
    fitted_value = models.FloatField(default=None, null=True, blank=True)
    total_value = models.FloatField(default=None, null=True, blank=True, db_index=True)
    zkb_points = models.PositiveIntegerField(
        default=None, null=True, blank=True, db_index=True
    )
    is_npc = models.BooleanField(default=None, null=True, blank=True, db_index=True)
    is_solo = models.BooleanField(default=None, null=True, blank=True, db_index=True)
    is_awox = models.BooleanField(default=None, null=True, blank=True, db_index=True)

    objects = EveKillmailManager()

    def __str__(self):
        return f"ID:{self.id}"

    def __repr__(self):
        return f"{type(self).__name__}(id={self.id})"

    def load_entities(self):
        """loads unknown entities for this killmail"""
        qs = EveEntity.objects.filter(id__in=self.entity_ids(), name="")
        qs.update_from_esi()

    def entity_ids(self) -> Set[int]:
        """IDs of all entity objects."""
        ids = super().entity_ids() | {self.solar_system_id}
        for attacker in self.attackers.all():
            ids |= attacker.entity_ids()
        ids.discard(None)
        return ids


class EveKillmailAttacker(_EveKillmailCharacter):
    """An attacker on a killmail in Eve Online."""

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

    def __str__(self) -> str:
        if self.character:
            return str(self.character)
        elif self.corporation:
            return str(self.corporation)
        elif self.alliance:
            return str(self.alliance)
        elif self.faction:
            return str(self.faction)
        return f"PK:{self.pk}"

    def entity_ids(self) -> Set[int]:
        """IDs of all entity objects."""
        ids = super().entity_ids() | {self.weapon_type.id}
        ids.discard(None)
        return ids


# class EveKillmail(models.Model): # outdated

#     id = models.BigIntegerField(primary_key=True)
#     time = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
#     solar_system = models.ForeignKey(
#         EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
#     )
#     updated_at = models.DateTimeField(auto_now=True)

#     objects = EveKillmailManager()

#     def __str__(self):
#         return f"ID:{self.id}"

#     def __repr__(self):
#         return f"{type(self).__name__}(id={self.id})"

#     def load_entities(self):
#         """loads unknown entities for this killmail"""
#         qs = EveEntity.objects.filter(id__in=self.entity_ids(), name="")
#         qs.update_from_esi()

#     def entity_ids(self) -> List[int]:
#         ids = [
#             self.victim.character_id,
#             self.victim.corporation_id,
#             self.victim.alliance_id,
#             self.victim.ship_type_id,
#             self.solar_system_id,
#         ]
#         for attacker in self.attackers.all():
#             ids += [
#                 attacker.character_id,
#                 attacker.corporation_id,
#                 attacker.alliance_id,
#                 attacker.ship_type_id,
#                 attacker.weapon_type_id,
#             ]
#         return [int(x) for x in ids if x is not None]


# class EveKillmailCharacter(models.Model):  # outdated

#     character = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )
#     corporation = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )
#     alliance = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )
#     faction = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )
#     ship_type = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )

#     class Meta:
#         abstract = True

#     def __str__(self) -> str:
#         if self.character:
#             return str(self.character)
#         elif self.corporation:
#             return str(self.corporation)
#         elif self.alliance:
#             return str(self.alliance)
#         elif self.faction:
#             return str(self.faction)
#         else:
#             return f"PK:{self.pk}"


# class EveKillmailVictim(EveKillmailCharacter):  # outdated

#     killmail = models.OneToOneField(
#         EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="victim"
#     )
#     damage_taken = models.BigIntegerField(default=None, null=True, blank=True)


# class EveKillmailAttacker(EveKillmailCharacter): # outdated

#     killmail = models.ForeignKey(
#         EveKillmail, on_delete=models.CASCADE, related_name="attackers"
#     )
#     damage_done = models.BigIntegerField(default=None, null=True, blank=True)
#     is_final_blow = models.BooleanField(
#         default=None, null=True, blank=True, db_index=True
#     )
#     security_status = models.FloatField(default=None, null=True, blank=True)
#     weapon_type = models.ForeignKey(
#         EveEntity,
#         on_delete=models.CASCADE,
#         default=None,
#         null=True,
#         blank=True,
#         related_name="+",
#     )


# class EveKillmailPosition(models.Model):  # outdated
#     killmail = models.OneToOneField(
#         EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="position"
#     )
#     x = models.FloatField(default=None, null=True, blank=True)
#     y = models.FloatField(default=None, null=True, blank=True)
#     z = models.FloatField(default=None, null=True, blank=True)


# class EveKillmailZkb(models.Model):  # outdated

#     killmail = models.OneToOneField(
#         EveKillmail, primary_key=True, on_delete=models.CASCADE, related_name="zkb"
#     )
#     location_id = models.PositiveIntegerField(
#         default=None, null=True, blank=True, db_index=True
#     )
#     hash = models.CharField(max_length=64, default="", blank=True)
#     fitted_value = models.FloatField(default=None, null=True, blank=True)
#     total_value = models.FloatField(default=None, null=True, blank=True, db_index=True)
#     points = models.PositiveIntegerField(
#         default=None, null=True, blank=True, db_index=True
#     )
#     is_npc = models.BooleanField(default=None, null=True, blank=True, db_index=True)
#     is_solo = models.BooleanField(default=None, null=True, blank=True, db_index=True)
#     is_awox = models.BooleanField(default=None, null=True, blank=True, db_index=True)


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
        redis_client = get_redis_client()
        return (
            SimpleMQ(redis_client, f"{__title__}_webhook_{self.pk}_{suffix}")
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
        brand_url = static_file_absolute_url("killtracker/killtracker_logo.png")
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

        Raises ValueError if message is incomplete
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
                retry_after = int(response.headers["Retry-After"]) + 2
            except (ValueError, KeyError):
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
        if name and url:
            return f"[{str(name)}]({str(url)})"
        return str(name)


class Tracker(models.Model):

    MAIN_MINIMUM_COUNT = 2
    MAIN_MINIMUM_SHARE = 0.25

    class ChannelPingType(models.TextChoices):
        NONE = "PN", "(none)"
        HERE = "PH", "@here"
        EVERYBODY = "PE", "@everybody"

    name = models.CharField(
        max_length=100,
        help_text="Name to identify tracker. Will be shown on alerts posts.",
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
            "black means no color selected."
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
            "When provided distance and jumps will be shown on killmail messages."
        ),
    )
    require_max_jumps = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Require all killmails to be max x jumps away from origin solar system."
        ),
    )
    require_max_distance = models.FloatField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Require all killmails to be max x LY away from origin solar system."
        ),
    )
    exclude_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="Exclude killmails with attackers from one of these alliances. ",
    )
    require_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="Only include killmails with attackers from one of these alliances. ",
    )
    exclude_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text="Exclude killmails with attackers from one of these corporations. ",
    )
    require_attacker_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails with attackers from one of these corporations. "
        ),
    )
    require_attacker_organizations_final_blow = models.BooleanField(
        default=False,
        blank=True,
        help_text=(
            "Only include killmails where at least one of the specified "
            "<b>required attacker corporations</b> or "
            "<b>required attacker alliances</b> "
            "has the final blow."
        ),
    )
    exclude_attacker_states = models.ManyToManyField(
        State,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Exclude killmails with characters belonging "
            "to users with these Auth states. "
        ),
    )
    require_attacker_states = models.ManyToManyField(
        State,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails with characters belonging "
            "to users with these Auth states. "
        ),
    )
    require_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where the victim belongs "
            "to one of these alliances. "
        ),
    )
    exclude_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Exclude killmails where the victim belongs to one of these alliances. "
        ),
    )
    require_victim_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where the victim belongs "
            "to one of these corporations. "
        ),
    )
    exclude_victim_corporations = models.ManyToManyField(
        EveCorporationInfo,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Exclude killmails where the victim belongs to one of these corporations. "
        ),
    )
    require_victim_states = models.ManyToManyField(
        State,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where the victim characters belong "
            "to users with these Auth states. "
        ),
    )
    identify_fleets = models.BooleanField(
        default=False,
        help_text="When true: kills are interpreted and shown as fleet kills.",
    )
    exclude_blue_attackers = models.BooleanField(
        default=False,
        help_text="Exclude killmails with blue attackers.",
    )
    require_blue_victim = models.BooleanField(
        default=False,
        help_text=(
            "Only include killmails where the victim has standing with our group."
        ),
    )
    require_min_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have at least given number of attackers.",
    )
    require_max_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have no more than max number of attackers.",
    )
    exclude_high_sec = models.BooleanField(
        default=False,
        help_text=(
            "Exclude killmails from high sec. "
            "Also exclude high sec systems in route finder for jumps from origin."
        ),
    )
    exclude_low_sec = models.BooleanField(
        default=False, help_text="Exclude killmails from low sec."
    )
    exclude_null_sec = models.BooleanField(
        default=False, help_text="Exclude killmails from null sec."
    )
    exclude_w_space = models.BooleanField(
        default=False, help_text="Exclude killmails from WH space."
    )
    require_regions = models.ManyToManyField(
        EveRegion,
        default=None,
        blank=True,
        related_name="+",
        help_text="Only include killmails that occurred in one of these regions. ",
    )
    require_constellations = models.ManyToManyField(
        EveConstellation,
        default=None,
        blank=True,
        related_name="+",
        help_text="Only include killmails that occurred in one of these regions. ",
    )
    require_solar_systems = models.ManyToManyField(
        EveSolarSystem,
        default=None,
        blank=True,
        related_name="+",
        help_text="Only include killmails that occurred in one of these regions. ",
    )
    require_min_value = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Require killmail's value to be greater "
            "or equal to the given value in M ISK."
        ),
    )
    require_attackers_ship_groups = models.ManyToManyField(
        EveGroup,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship groups. "
        ),
    )
    require_attackers_ship_types = models.ManyToManyField(
        EveType,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where at least one attacker "
            "is flying one of these ship types. "
        ),
    )
    require_victim_ship_groups = models.ManyToManyField(
        EveGroup,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where victim is flying one of these ship groups. "
        ),
    )
    require_victim_ship_types = models.ManyToManyField(
        EveType,
        related_name="+",
        default=None,
        blank=True,
        help_text=(
            "Only include killmails where victim is flying one of these ship types. "
        ),
    )
    exclude_npc_kills = models.BooleanField(
        default=False, help_text="Exclude npc kills."
    )
    require_npc_kills = models.BooleanField(
        default=False, help_text="Only include killmails that are npc kills."
    )
    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.CASCADE,
        help_text="Webhook URL for a channel on Discord to sent all alerts to.",
    )
    ping_type = models.CharField(
        max_length=2,
        choices=ChannelPingType.choices,
        default=ChannelPingType.NONE,
        verbose_name="channel pings",
        help_text="Option to ping every member of the channel.",
    )
    ping_groups = models.ManyToManyField(
        Group,
        default=None,
        blank=True,
        verbose_name="group pings",
        related_name="+",
        help_text="Option to ping specific group members. ",
    )
    is_posting_name = models.BooleanField(
        default=True, help_text="Whether posted messages include the tracker's name."
    )
    is_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Toogle for activating or deactivating a tracker.",
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
        """returns True if tracker has a clause that needs the killmails's solar system"""
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
        """Run tracker on a killmail and see if it matches

        Args:
        - killmail: Killmail to process
        - ignore_max_age: Whether to discord killmails that are older then the defined threshold

        Returns:
        - Copy of killmail with added tracker info if it matches or None if there is no match
        """
        threshold_date = now() - timedelta(
            minutes=KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
        )
        if not ignore_max_age and killmail.time < threshold_date:
            return None

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
            EveType.objects.bulk_get_or_create_esi(
                ids=killmail.ship_type_distinct_ids()
            )

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
                    killmail.zkb.total_value is not None
                    and killmail.zkb.total_value >= self.require_min_value * 1_000_000
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
                    alliance_id__in=killmail.attackers_distinct_alliance_ids()
                ).exists()

            if is_matching and self.exclude_attacker_corporations.exists():
                is_matching = self.exclude_attacker_corporations.exclude(
                    corporation_id__in=killmail.attackers_distinct_corporation_ids()
                ).exists()

            if is_matching:
                if self.require_attacker_organizations_final_blow:
                    attacker_final_blow = killmail.attacker_final_blow()
                    is_matching = bool(attacker_final_blow) and (
                        (
                            bool(attacker_final_blow.alliance_id)
                            and self.require_attacker_alliances.filter(
                                alliance_id=attacker_final_blow.alliance_id
                            ).exists()
                        )
                        | (
                            bool(attacker_final_blow.corporation_id)
                            and self.require_attacker_corporations.filter(
                                corporation_id=attacker_final_blow.corporation_id
                            ).exists()
                        )
                    )
                else:
                    if is_matching and self.require_attacker_alliances.exists():
                        is_matching = self.require_attacker_alliances.filter(
                            alliance_id__in=killmail.attackers_distinct_alliance_ids()
                        ).exists()
                    if is_matching and self.require_attacker_corporations.exists():
                        is_matching = self.require_attacker_corporations.filter(
                            corporation_id__in=killmail.attackers_distinct_corporation_ids()
                        ).exists()

            if is_matching and self.require_victim_alliances.exists():
                is_matching = self.require_victim_alliances.filter(
                    alliance_id=killmail.victim.alliance_id
                ).exists()

            if is_matching and self.exclude_victim_alliances.exists():
                is_matching = self.exclude_victim_alliances.exclude(
                    alliance_id=killmail.victim.alliance_id
                ).exists()

            if is_matching and self.require_victim_corporations.exists():
                is_matching = self.require_victim_corporations.filter(
                    corporation_id=killmail.victim.corporation_id
                ).exists()

            if is_matching and self.exclude_victim_corporations.exists():
                is_matching = self.exclude_victim_corporations.exclude(
                    corporation_id=killmail.victim.corporation_id
                ).exists()

            if is_matching and self.require_attacker_states.exists():
                is_matching = User.objects.filter(
                    profile__state__in=list(self.require_attacker_states.all()),
                    character_ownerships__character__character_id__in=(
                        killmail.attackers_distinct_character_ids()
                    ),
                ).exists()

            if is_matching and self.exclude_attacker_states.exists():
                is_matching = not User.objects.filter(
                    profile__state__in=list(self.exclude_attacker_states.all()),
                    character_ownerships__character__character_id__in=(
                        killmail.attackers_distinct_character_ids()
                    ),
                ).exists()

            if is_matching and self.require_victim_states.exists():
                is_matching = User.objects.filter(
                    profile__state__in=list(self.require_victim_states.all()),
                    character_ownerships__character__character_id=(
                        killmail.victim.character_id
                    ),
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
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=set(killmail.attackers_ship_type_ids())
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
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=set(killmail.attackers_ship_type_ids())
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
            threshold = max(
                len(killmail.attackers) * cls.MAIN_MINIMUM_SHARE,
                cls.MAIN_MINIMUM_COUNT,
            )
            if max_count >= threshold:
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
            threshold = max(
                len(killmail.attackers) * cls.MAIN_MINIMUM_SHARE,
                cls.MAIN_MINIMUM_COUNT,
            )
            if max_count >= threshold:
                return sorted(ship_groups_2, key=lambda x: x.count).pop()

        return None

    def generate_killmail_message(
        self, killmail: Killmail, intro_text: str = None
    ) -> int:
        """generate a message from given killmail and enqueue for later sending

        returns new queue size
        """
        from .core import discord_messages

        content = discord_messages.create_content(self, intro_text)
        embed = discord_messages.create_embed(self, killmail)
        return self.webhook.enqueue_message(content=content, embeds=[embed])
