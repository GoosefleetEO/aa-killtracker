from datetime import timedelta
import json
from time import sleep

import dhooks_lite

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo
from allianceauth.services.hooks import get_extension_logger

from eveuniverse.models import EveSolarSystem, EveGroup, EveEntity

from . import __title__
from .managers import KillmailManager, TrackedKillmailManager
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

WEBHOOK_URL = "https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO"  # noqa


DEFAULT_MAX_AGE_HOURS = 4
EVE_CATEGORY_ID_SHIPS = 6


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
        related_name="%(class)s_character_set",
    )
    corporation = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_corporation_set",
    )
    alliance = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_alliance_set",
    )
    faction = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_faction_set",
    )
    ship_type = models.ForeignKey(
        EveEntity,
        on_delete=models.CASCADE,
        default=None,
        null=True,
        blank=True,
        related_name="%(class)s_shiptype_set",
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
    is_default = models.BooleanField(
        default=False,
        help_text=("When true this webhook will be preset for newly created trackers"),
    )

    def __str__(self):
        return self.name

    def __repr__(self):
        return "{}(id={}, name='{}')".format(
            self.__class__.__name__, self.id, self.name
        )

    def send_test_notification(self) -> str:
        """Sends a test notification to this webhook and returns send report"""
        hook = dhooks_lite.Webhook(self.url)
        response = hook.execute(
            _(
                "This is a test notification from %s.\n"
                "The webhook appears to be correctly configured."
            )
            % __title__,
            wait_for_response=True,
        )
        if response.status_ok:
            send_report_json = json.dumps(response.content, indent=4, sort_keys=True)
        else:
            send_report_json = "HTTP status code {}".format(response.status_code)
        return send_report_json


class Tracker(models.Model):

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
    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        help_text="Webhook URL for a channel on Discord to sent all alerts to",
    )
    origin_solar_system = models.ForeignKey(
        EveSolarSystem,
        on_delete=models.SET_DEFAULT,
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Solar system to calculate ranges and jumps from. "
            "(usually the staging system)."
        ),
    )
    exclude_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="exclude_attacker_alliances_set",
        default=None,
        blank=True,
        help_text="exclude killmails with attackers are from one of these alliances",
    )
    required_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="required_attacker_alliances_set",
        default=None,
        blank=True,
        help_text="only include killmails with attackers from one of these alliances",
    )
    require_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name="require_victim_alliances_set",
        default=None,
        blank=True,
        help_text=(
            "only include killmails where the victim belongs to one of these alliances"
        ),
    )
    identify_fleets = models.BooleanField(
        default=False,
        help_text="when true: kills are interpreted and shown as fleet kills",
    )
    """
    exclude_blue_attackers = models.BooleanField(
        default=False, 
        help_text=(
            'exclude killmails where the main group of the attackers '
            'has blue standing with our alliance'
        )
    )
    """
    exclude_high_sec = models.BooleanField(
        default=False,
        help_text=(
            "exclude killmails from high sec."
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
    min_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Require killmails to have at least given amount of attackers",
    )
    max_attackers = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Exclude killmails that exceed given amount of attackers",
    )
    max_jumps = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Exclude killmails which are more than x jumps away"
            "from the origin solar system"
        ),
    )
    min_value = models.PositiveIntegerField(
        default=None,
        null=True,
        blank=True,
        help_text="Exclude killmails with a value below the given amount",
    )
    max_distance = models.FloatField(
        default=None,
        null=True,
        blank=True,
        help_text=(
            "Exclude killmails which are farer away from the origin solar system "
            "than the given distance in lightyears"
        ),
    )
    require_attackers_ship_groups = models.ManyToManyField(
        EveGroup,
        limit_choices_to={"eve_category_id": EVE_CATEGORY_ID_SHIPS},
        related_name="require_attackers_ship_groups_set",
        default=None,
        blank=True,
        help_text=(
            "exclude killmails where attackers are flying one of these ship groups"
        ),
    )
    max_age = models.PositiveIntegerField(
        default=DEFAULT_MAX_AGE_HOURS,
        null=True,
        blank=True,
        help_text=(
            "ignore killmails that are older than the given number in hours "
            "(sometimes killmails appear belated on ZKB - "
            "this feature ensures they don't create new alerts)"
        ),
    )
    is_enabled = models.BooleanField(
        default=True, help_text="toogle for activating or deactivating a tracker"
    )

    def __str__(self):
        return self.name

    def calculate_killmails(self, force: bool = False) -> set:
        """marks all killmails that comply with all criteria of this tracker
        and also adds additional information to killmails, like distance from origin
        
        Params:
        - force: run again for already processed killmails
        """
        killmail_count = TrackedKillmail.objects.generate(tracker=self, force=force)
        logger.info("Tracker %s: Processing %d fresh killmails", self, killmail_count)

        # apply all filters from tracker to determine matching killmails
        matching_qs = TrackedKillmail.objects.filter(
            tracker=self, is_matching__isnull=True
        ).prefetch_related()

        if self.max_age:
            threshold_date = now() - timedelta(hours=self.max_age)
            matching_qs = matching_qs.exclude(killmail__time__lt=threshold_date)

        if self.exclude_high_sec:
            matching_qs = matching_qs.exclude(is_high_sec=True)

        if self.exclude_low_sec:
            matching_qs = matching_qs.exclude(is_low_sec=True)

        if self.exclude_null_sec:
            matching_qs = matching_qs.exclude(is_null_sec=True)

        if self.exclude_w_space:
            matching_qs = matching_qs.exclude(is_w_space=True)

        if self.min_attackers:
            matching_qs = matching_qs.exclude(attackers_count__lt=self.min_attackers)

        if self.max_attackers:
            matching_qs = matching_qs.exclude(attackers_count__gt=self.max_attackers)

        if self.min_value:
            matching_qs = matching_qs.exclude(
                killmail__zkb__total_value__lt=self.min_value
            )

        if self.max_distance:
            matching_qs = matching_qs.exclude(distance__isnull=True).exclude(
                distance__gt=self.max_distance
            )

        if self.max_jumps:
            matching_qs = matching_qs.exclude(jumps__isnull=True).exclude(
                jumps__gt=self.max_jumps
            )

        if self.exclude_attacker_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.exclude_attacker_alliances)
            matching_qs = matching_qs.exclude(
                killmail__attackers__alliance__id__in=alliance_ids
            )

        if self.required_attacker_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.required_attacker_alliances)
            matching_qs = matching_qs.filter(
                killmail__attackers__alliance__id__in=alliance_ids
            )

        if self.require_victim_alliances.count() > 0:
            alliance_ids = self._extract_alliance_ids(self.require_victim_alliances)
            matching_qs = matching_qs.filter(
                killmail__victim__alliance__id__in=alliance_ids
            )

        # store which killmails match with this tracker
        matching_killmail_ids = set(sorted(set(matching_qs.killmail_ids())))
        matching_qs.update(is_matching=True)
        logger.info(
            "Tracker %s: Found %d matching killmails", self, len(matching_killmail_ids),
        )
        logger.info(
            "Tracker %s: Matching killmail IDs: %s", self, matching_killmail_ids,
        )
        return matching_killmail_ids

    @staticmethod
    def _extract_alliance_ids(alliances: list) -> list:
        return [
            int(alliance_id)
            for alliance_id in alliances.values_list("alliance_id", flat=True)
        ]

    def send_matching_to_webhook(self, resend: bool = False) -> int:
        """sends all matching killmails for this tracker to the webhook
        
        returns number of successfull sent messages
        """
        if not self.webhook:
            logger.warning("Tracker %s: No webhook configured - skipping sending", self)
            return 0

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
            try:
                matching_killmail.send_to_webhook(self.webhook.url)
            except Exception as ex:
                logger.exception(ex)

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
    is_high_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_low_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_null_sec = models.BooleanField(default=None, null=True, db_index=True)
    is_w_space = models.BooleanField(default=None, null=True, db_index=True)

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

    def send_to_webhook(self, webhook_url: str = WEBHOOK_URL):
        killmail = self.killmail
        if killmail.victim.character:
            victim_str = (
                f"{self._entity_zkb_link(killmail.victim.character)} "
                f"{self._entity_zkb_link(killmail.victim.corporation)} "
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
        description = (
            f"{victim_str} lost their **{killmail.victim.ship_type.name}** "
            f"in {self._entity_zkb_link(killmail.solar_system)} "
            f"worth **{value_mio} M** ISK.\n"
            f"Final blow by {attacker_str} "
            f"in a **{attacker.ship_type.name}**.\n"
            f"Attackers: {killmail.attackers.count()}"
        )
        if self.tracker.origin_solar_system:
            origin_solar_system_link = (
                f"[{self.tracker.origin_solar_system.name}]"
                f"({self._entity_dotlan_link(self.tracker.origin_solar_system)})"
            )
            distance = f"{self.distance:,.2f}" if self.distance is not None else "?"
            jumps = self.jumps if self.jumps is not None else "?"
            description += (
                f"\nDistance from {origin_solar_system_link}: "
                f"{distance} LY | {jumps} jumps"
            )

        title = f"{killmail.solar_system.name} | {victim_name} | Killmail"
        thumbnail_url = killmail.victim.ship_type.icon_url()
        footer_text = "zKillboard"
        zkb_killmail_url = f"{self.killmail.ZKB_KILLMAIL_BASEURL}{self.id}/"
        embed = dhooks_lite.Embed(
            description=description,
            title=title,
            url=zkb_killmail_url,
            thumbnail=dhooks_lite.Thumbnail(url=thumbnail_url),
            footer=dhooks_lite.Footer(text=footer_text),
            timestamp=killmail.time,
        )
        logger.info(
            "Tracker %s: Sending alert to Discord for killmail %s",
            self.tracker,
            self.killmail,
        )
        hook = dhooks_lite.Webhook(url=webhook_url, username="killtracker")
        response = hook.execute(embeds=[embed], wait_for_response=True)
        logger.debug("headers: %s", response.headers)
        logger.debug("status_code: %s", response.status_code)
        logger.debug("content: %s", response.content)
        self.date_sent = now()
        self.save()

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
