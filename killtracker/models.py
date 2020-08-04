from copy import deepcopy
from datetime import timedelta
from time import sleep
from urllib.parse import urljoin
from typing import Optional, List, Tuple

import dhooks_lite
from simple_mq import SimpleMQ

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.evelinks import eveimageserver, zkillboard, dotlan
from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

from eveuniverse.helpers import meters_to_ly, EveEntityNameResolver
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
from .core.killmails import EntityCount, Killmail, TrackerInfo
from .managers import EveKillmailManager
from .utils import LoggerAddTag, get_site_base_url, humanize_value

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

DEFAULT_MAX_AGE_HOURS = 4
EVE_CATEGORY_ID_SHIP = 6
EVE_CATEGORY_ID_STRUCTURE = 65


# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
DISCORD_SEND_DELAY = 2


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
        return f"EveKillmail(id={self.id})"

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
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
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
    """A destination for forwarding killmails"""

    ZKB_KILLMAIL_BASEURL = "https://zkillboard.com/kill/"
    ICON_SIZE = 128

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
        db_index=True,
        help_text="whether notifications are currently sent to this webhook",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._queue = SimpleMQ(
            cache.get_master_client(), f"{__title__}_webhook_{self.pk}"
        )

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return "{}(id={}, name='{}')".format(
            self.__class__.__name__, self.id, self.name
        )

    def add_killmail_to_queue(self, killmail: Killmail) -> int:
        """Adds killmail to queue for later sending
        
        Returns updated size of queue
        """
        return self._queue.enqueue(killmail.asjson())

    def send_queued_killmails(self) -> int:
        """sends all killmails in the queue to this webhook
        
        returns number of successfull sent messages

        Killmails that could not be sent are put back into the queue for later retry
        """
        failed_killmails = list()
        killmail_counter = 0
        while True:
            message = self._queue.dequeue()
            if message:
                killmail = Killmail.from_json(message)
                logger.debug(
                    "Sending killmail with ID %d to webhook %s", killmail.id, self
                )
                sleep(DISCORD_SEND_DELAY)
                if self.send_killmail(killmail):
                    killmail_counter += 1
                else:
                    failed_killmails.append(killmail)
            else:
                break

        if failed_killmails:
            for killmail in failed_killmails:
                self.add_killmail_to_queue(killmail)

        return killmail_counter

    def queue_size(self) -> int:
        """returns current size of the queue"""
        return self._queue.size()

    def clear_queue(self) -> int:
        """deletes all killmails from the queue. Return number of cleared messages."""
        counter = 0
        while True:
            y = self._queue.dequeue()
            if y is None:
                break
            else:
                counter += 1

        return counter

    def send_killmail(self, killmail: Killmail, intro_text: str = None) -> bool:
        """send given killmail to webhook
        
        returns True if successful, else False        
        """
        resolver = EveEntity.objects.bulk_resolve_names(ids=killmail.entity_ids())

        # victim
        if killmail.victim.corporation_id:
            victim_corporation_zkb_link = self._corporation_zkb_link(
                killmail.victim.corporation_id, resolver
            )
        else:
            victim_corporation_zkb_link = ""

        if killmail.victim.character_id:
            victim_character_zkb_link = self._character_zkb_link(
                killmail.victim.character_id, resolver,
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
            solar_system = EveSolarSystem.objects.select_related(
                "eve_constellation__eve_region"
            ).get(id=killmail.solar_system_id)
            solar_system_link = self._convert_to_discord_link(
                name=solar_system.name, url=dotlan.solar_system_url(solar_system.name)
            )
            region_name = solar_system.eve_constellation.eve_region.name
            solar_system_text = f"{solar_system_link} ({region_name})"
        else:
            solar_system_text = ""

        # tracker info
        tracker = None
        show_as_fleetkill = False
        distance_text = ""
        main_org_text = ""
        main_org_name = ""
        main_org_icon_url = eveimageserver.alliance_logo_url(1, size=self.ICON_SIZE)
        main_ship_group_text = ""
        tracked_ship_types_text = ""
        if killmail.tracker_info:
            tracker = Tracker.objects.get(pk=killmail.tracker_info.tracker_pk)
            show_as_fleetkill = tracker.identify_fleets
            if tracker.origin_solar_system:
                origin_solar_system_link = self._convert_to_discord_link(
                    name=tracker.origin_solar_system.name,
                    url=dotlan.solar_system_url(tracker.origin_solar_system.name),
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

        if intro_text:
            intro = f"{intro_text}\n{intro}"

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
            return True
        else:
            logger.warning(
                "Failed to send message to Discord. HTTP status code: %d, response: %s",
                response.status_code,
                response.content,
            )
            return False

    @classmethod
    def _character_zkb_link(
        cls, entity_id: int, resolver: EveEntityNameResolver
    ) -> str:
        return cls._convert_to_discord_link(
            name=resolver.to_name(entity_id), url=zkillboard.character_url(entity_id)
        )

    @classmethod
    def _corporation_zkb_link(
        cls, entity_id: int, resolver: EveEntityNameResolver
    ) -> str:
        return cls._convert_to_discord_link(
            name=resolver.to_name(entity_id), url=zkillboard.corporation_url(entity_id)
        )

    @classmethod
    def _alliance_zkb_link(cls, entity_id: int, resolver: EveEntityNameResolver) -> str:
        return cls._convert_to_discord_link(
            name=resolver.to_name(entity_id), url=zkillboard.alliance_url(entity_id)
        )

    @classmethod
    def _convert_to_discord_link(cls, name: str, url: str) -> str:
        return f"[{str(name)}]({str(url)})"

    def send_test_message(self, killmail_id: int = 82700336) -> Tuple[str, bool]:
        """Sends a test notification to this webhook and returns send report"""
        try:
            success = self.send_killmail(
                Killmail.create_from_zkb_api(killmail_id=killmail_id),
                f"Test notification for webhook {self.name}:",
            )
        except Exception as ex:
            logger.warning(
                "Failed to send test notification to webhook %s: %s",
                self,
                ex,
                exc_info=True,
            )
            return str(ex), False
        else:
            return "(no info)", success

    @staticmethod
    def default_avatar_url() -> str:
        """avatar url for all messages"""
        return urljoin(
            get_site_base_url(),
            staticfiles_storage.url("killtracker/killtracker_logo.png"),
        )

    @staticmethod
    def zkb_icon_url() -> str:
        """avatar url for all messages"""
        return urljoin(
            get_site_base_url(), staticfiles_storage.url("killtracker/zkb_icon.png"),
        )

    @staticmethod
    def default_username() -> str:
        """avatar username for all messages"""
        return __title__


class Tracker(models.Model):

    MAIN_MINIMUM_COUNT = 2
    MAIN_MINIMUM_SHARE = 0.25

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
        help_text="Require killmail's value to be greater or equal to the given value in M ISK",
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
        default=True,
        db_index=True,
        help_text="toogle for activating or deactivating a tracker",
    )

    def __str__(self) -> str:
        return self.name

    @property
    def has_localization_filter(self) -> bool:
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

    def clean(self) -> None:
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
            self.origin_solar_system or self.has_localization_filter
        ):
            solar_system = (
                EveSolarSystem.objects.filter(id=killmail.solar_system_id)
                .select_related("eve_constellation", "eve_constellation__eve_region")
                .first()
            )
            if solar_system:
                is_high_sec = solar_system.is_high_sec
                is_low_sec = solar_system.is_low_sec
                is_null_sec = solar_system.is_null_sec
                is_w_space = solar_system.is_w_space
                if self.origin_solar_system:
                    distance = meters_to_ly(
                        self.origin_solar_system.distance_to(solar_system)
                    )
                    jumps = self.origin_solar_system.jumps_to(solar_system)

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

            if is_matching and self.require_regions.count() > 0:
                is_matching = solar_system and bool(
                    self.require_regions.filter(
                        id=solar_system.eve_constellation.eve_region_id
                    )
                )

            if is_matching and self.require_constellations.count() > 0:
                is_matching = solar_system and bool(
                    self.require_constellations.filter(
                        id=solar_system.eve_constellation_id
                    )
                )

            if is_matching and self.require_solar_systems.count() > 0:
                is_matching = solar_system and bool(
                    self.require_solar_systems.filter(id=solar_system.id)
                )

            if is_matching and self.exclude_attacker_alliances.count() > 0:
                is_matching = bool(
                    self.exclude_attacker_alliances.exclude(
                        alliance_id__in=killmail.attackers_alliance_ids()
                    )
                )

            if is_matching and self.require_attacker_alliances.count() > 0:
                is_matching = bool(
                    self.require_attacker_alliances.filter(
                        alliance_id__in=killmail.attackers_alliance_ids()
                    )
                )

            if is_matching and self.exclude_attacker_corporations.count() > 0:
                is_matching = bool(
                    self.exclude_attacker_corporations.exclude(
                        corporation_id__in=killmail.attackers_corporation_ids()
                    )
                )

            if is_matching and self.require_attacker_corporations.count() > 0:
                is_matching = bool(
                    self.require_attacker_corporations.filter(
                        corporation_id__in=killmail.attackers_corporation_ids()
                    )
                )

            if is_matching and self.require_victim_alliances.count() > 0:
                is_matching = bool(
                    self.require_victim_alliances.filter(
                        alliance_id=killmail.victim.alliance_id
                    )
                )

            if is_matching and self.require_victim_corporations.count() > 0:
                is_matching = bool(
                    self.require_victim_corporations.filter(
                        corporation_id=killmail.victim.corporation_id
                    )
                )

            if is_matching and self.require_victim_ship_groups.count() > 0:
                is_matching = bool(
                    EveType.objects.filter(
                        eve_group__in=self.require_victim_ship_groups.all(),
                        id=killmail.victim.ship_type_id,
                    ).select_related()
                )

            if is_matching and self.require_attackers_ship_groups.count() > 0:
                attackers_ship_type_ids = killmail.attackers_ship_type_ids()
                ship_types_matching_qs = (
                    EveType.objects.filter(id__in=attackers_ship_type_ids)
                    .filter(eve_group__in=self.require_attackers_ship_groups.all())
                    .select_related("eve_group")
                )
                is_matching = bool(ship_types_matching_qs)
                matching_ship_type_ids = list(
                    ship_types_matching_qs.values_list("id", flat=True)
                )

            if is_matching and self.require_attackers_ship_types.count() > 0:
                attackers_ship_type_ids = killmail.attackers_ship_type_ids()
                ship_types_matching_qs = EveType.objects.filter(
                    id__in=attackers_ship_type_ids
                ).filter(id__in=self.require_attackers_ship_types.all())
                is_matching = bool(ship_types_matching_qs)
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
