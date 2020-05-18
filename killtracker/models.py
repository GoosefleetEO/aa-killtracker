from datetime import timedelta
import json
import logging
from time import sleep
import urllib

import dhooks_lite

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo
from evesde.models import (
    EveSolarSystem, EveGroup, EveType, EveRegion
)
from evesde.helpers import meters_to_ly

from . import __title__
from .managers import EveEntityManager, KillmailManager

logger = logging.getLogger('allianceauth')
WEBHOOK_URL = 'https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO'  # noqa


DEFAULT_MAX_AGE_HOURS = 4

# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
DISCORD_SEND_DELAY = 1


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        managed = False                         
        default_permissions = ()
        permissions = ( 
            ('basic_access', 'Can access this app'), 
        )


class EveEntity(models.Model):
    
    EVE_IMAGESERVER_BASE_URL = 'https://images.evetech.net'
    ZKB_ENTITY_URL_BASE = 'https://zkillboard.com/'

    CATEGORY_ALLIANCE = 'alliance'
    CATEGORY_CHARACTER = 'character'
    CATEGORY_CONSTELLATION = 'constellation'
    CATEGORY_CORPORATION = 'corporation'
    CATEGORY_FACTION = 'faction'
    CATEGORY_INVENTORY_TYPE = 'inventory_type'
    CATEGORY_REGION = 'region'    
    CATEGORY_SOLAR_SYSTEM = 'solar_system'
    CATEGORY_STATION = 'station'    

    CATEGORY_CHOICES = (
        (CATEGORY_ALLIANCE, 'alliance'),
        (CATEGORY_CHARACTER, 'character'),
        (CATEGORY_CONSTELLATION, 'constellation'),
        (CATEGORY_CORPORATION, 'corporation'),
        (CATEGORY_FACTION, 'faction'),
        (CATEGORY_INVENTORY_TYPE, 'inventory_type'),
        (CATEGORY_REGION, 'region'),
        (CATEGORY_SOLAR_SYSTEM, 'solar_system'),
        (CATEGORY_STATION, 'station'),        
    )
    
    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(
        max_length=100, default='', blank=True
    )
    category = models.CharField(
        max_length=16, choices=CATEGORY_CHOICES, default=None, null=True, blank=True,
    )
    last_updated = models.DateTimeField(
        default=None, null=True, blank=True, db_index=True,
    )

    objects = EveEntityManager()

    def __str__(self):
        if self.name:
            return self.name
        else:
            return f'ID:{self.id}'
    
    def __repr__(self):
        return (
            f'{type(self).__name__}(id={self.id}, name=\'{self.name}\', '
            f'category=\'{self.category}\')'
        )

    @property
    def zkb_url(self) -> str:
        map_category_2_path = {
            self.CATEGORY_ALLIANCE: 'alliance',
            self.CATEGORY_CHARACTER: 'character',
            self.CATEGORY_CORPORATION: 'corporation',            
            self.CATEGORY_INVENTORY_TYPE: 'system',
            self.CATEGORY_REGION: 'region',
            self.CATEGORY_SOLAR_SYSTEM: 'system',
        }
        if self.category in map_category_2_path:
            path = map_category_2_path[self.category]
            return f'{self.ZKB_ENTITY_URL_BASE}{path}/{self.id}/'
        else:
            return ''

    @property
    def zkb_link(self) -> str:
        url = self.zkb_url
        if url:
            return f'[{self.name}]({url})'
        else:
            return f'{self.name}'

    def update_from_esi(self):
        EveEntity.objects.get(self.id).update_from_esi()
    
    def icon_url(self, size: int = 64) -> str:
        if self.category == self.CATEGORY_INVENTORY_TYPE:
            if size < 32 or size > 1024 or (size % 2 != 0):
                raise ValueError("Invalid size: {}".format(size))

            url = '{}/types/{}/icon'.format(
                self.EVE_IMAGESERVER_BASE_URL,
                int(self.id)
            )
            if size:
                args = {'size': int(size)}
                url += '?{}'.format(urllib.parse.urlencode(args))

            return url
        
        else:
            raise NotImplementedError()

    def get_pendant_object(self) -> object:
        """returns the corresponding full objects for this entity,
        e.g. EveSolarSystem for an entity with category "solar system"        
        """
        if self.category == self.CATEGORY_SOLAR_SYSTEM:
            return EveSolarSystem.objects.get(solar_system_id=self.id)        
        elif self.category == self.CATEGORY_REGION:
            return EveRegion.objects.get(region_id=self.id)
        elif self.category == self.CATEGORY_INVENTORY_TYPE:
            return EveType.objects.get(type_id=self.id)
        else:
            raise NotImplementedError()


class Killmail(models.Model):
    
    ZKB_KILLMAIL_BASEURL = 'https://zkillboard.com/kill/'

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True)
    solar_system = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    
    objects = KillmailManager()
    
    def __str__(self):
        return f'ID:{self.id}'

    def __repr__(self):
        return f'Killmail(id={self.id})'

    def update_from_esi(self):
        ids = [            
            self.victim.character_id,
            self.victim.corporation_id,
            self.victim.alliance_id,
            self.victim.ship_type_id,
            self.solar_system_id            
        ]
        for attacker in self.attackers.all():
            ids += [                
                attacker.character_id,
                attacker.corporation_id,
                attacker.alliance_id,
                attacker.ship_type_id,
                attacker.weapon_type_id,
            ]
        ids = [x for x in ids if x is not None]
        qs = EveEntity.objects.filter(id__in=ids, name='')
        qs.update_from_esi()
        
    def send_to_webhook(self, webhook_url: str = WEBHOOK_URL):        
        zkb_killmail_url = f'{self.ZKB_KILLMAIL_BASEURL}{self.id}/'            
        value_mio = int(self.zkb.total_value / 1000000)        
        if self.victim.character:
            victim_str = (                
                f'{self.victim.character.zkb_link} '
                f'({self.victim.corporation.zkb_link}) '
            )
            victim_name = self.victim.character.name
        else:
            victim_str = f'{self.victim.corporation.zkb_link}'
            victim_name = self.victim.corporation.name

        attacker = self.attackers.get(is_final_blow=True)
        if attacker.character and attacker.corporation:
            attacker_str = (
                f'{attacker.character.zkb_link} '
                f'({attacker.corporation.zkb_link})'
            )
        elif attacker.corporation:
            attacker_str = f'{attacker.corporation.zkb_link}'        
        elif attacker.faction:
            attacker_str = f'**{attacker.faction.name}**'
        else:
            attacker_str = '(Unknown attacker)'

        description = (
            f'{victim_str} lost their **{self.victim.ship_type.name}** '
            f'in {self.solar_system.zkb_link} worth **{value_mio} M** ISK.\n'
            f'Final blow by {attacker_str} '
            f'in a **{attacker.ship_type.name}**.\n'
            f'Attackers: {self.attackers.count()}'
        )
        title = \
            f'{self.solar_system.name} | {victim_name} | Killmail'
        thumbnail_url = self.victim.ship_type.icon_url()
        footer_text = 'zKillboard'
        embed = dhooks_lite.Embed(
            description=description,
            title=title,
            url=zkb_killmail_url,
            thumbnail=dhooks_lite.Thumbnail(url=thumbnail_url),
            footer=dhooks_lite.Footer(text=footer_text),
            timestamp=self.time
        )            
        logger.info('Sending self to Discord')
        hook = dhooks_lite.Webhook(url=webhook_url, username='killtracker')
        hook.execute(embeds=[embed], wait_for_response=True)            
        self.is_processed = True
        self.save()


class KillmailCharacter(models.Model):
    
    character = models.ForeignKey(
        EveEntity, 
        on_delete=models.CASCADE, 
        default=None, null=True, 
        blank=True, 
        related_name='%(class)s_character_set'
    )
    corporation = models.ForeignKey(
        EveEntity, 
        on_delete=models.CASCADE, 
        default=None, 
        null=True, 
        blank=True, 
        related_name='%(class)s_corporation_set'
    )
    alliance = models.ForeignKey(
        EveEntity, 
        on_delete=models.CASCADE, 
        default=None, 
        null=True, 
        blank=True, 
        related_name='%(class)s_alliance_set'
    )
    faction = models.ForeignKey(
        EveEntity, 
        on_delete=models.CASCADE, 
        default=None, 
        null=True, 
        blank=True, 
        related_name='%(class)s_faction_set'
    )    
    ship_type = models.ForeignKey(
        EveEntity, 
        on_delete=models.CASCADE, 
        default=None, 
        null=True, 
        blank=True, 
        related_name='%(class)s_shiptype_set'
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
            return f'PK:{self.pk}'


class KillmailVictim(KillmailCharacter):

    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name='victim'
    )
    damage_taken = models.BigIntegerField(default=None, null=True, blank=True)
    

class KillmailAttacker(KillmailCharacter):

    killmail = models.ForeignKey(
        Killmail, on_delete=models.CASCADE, related_name='attackers'
    )
    damage_done = models.BigIntegerField(default=None, null=True, blank=True)
    is_final_blow = models.BooleanField(default=None, null=True, blank=True)
    security_status = models.FloatField(default=None, null=True, blank=True)
    weapon_type = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )


class KillmailPosition(models.Model):
    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name='position'
    )
    x = models.FloatField(default=None, null=True, blank=True)
    y = models.FloatField(default=None, null=True, blank=True)
    z = models.FloatField(default=None, null=True, blank=True)


class KillmailZkb(models.Model):

    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name='zkb'
    )
    location_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    hash = models.CharField(max_length=64, default='', blank=True)
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
        (TYPE_DISCORD, _('Discord Webhook')),
    ]

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text='short name to identify this webhook'
    )
    webhook_type = models.IntegerField(
        choices=TYPE_CHOICES,
        default=TYPE_DISCORD,
        help_text='type of this webhook'
    )
    url = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            'URL of this webhook, e.g. '
            'https://discordapp.com/api/webhooks/123456/abcdef'
        )
    )
    notes = models.TextField(
        null=True,
        default=None,
        blank=True,
        help_text='you can add notes about this webhook here if you want'
    )    
    is_active = models.BooleanField(
        default=True,
        help_text='whether notifications are currently sent to this webhook'
    )
    is_default = models.BooleanField(
        default=False,
        help_text=(
            'When true this webhook will be preset for newly created trackers'            
        )
    )

    def __str__(self):
        return self.name

    def __repr__(self):
        return '{}(id={}, name=\'{}\')'.format(
            self.__class__.__name__,
            self.id,
            self.name
        )

    def send_test_notification(self) -> str:
        """Sends a test notification to this webhook and returns send report"""
        hook = dhooks_lite.Webhook(self.url)
        response = hook.execute(
            _(
                'This is a test notification from %s.\n'
                'The webhook appears to be correctly configured.'
            ) % __title__,
            wait_for_response=True
        )
        if response.status_ok:
            send_report_json = json.dumps(
                response.content, indent=4, sort_keys=True
            )
        else:
            send_report_json = 'HTTP status code {}'.format(
                response.status_code
            )
        return send_report_json


class Tracker(models.Model):

    name = models.CharField(
        max_length=100, 
        help_text='name to identify tracker. Will be shown on alerts posts.',
        unique=True
    )
    description = models.TextField(
        default='', 
        blank=True, 
        help_text=(
            'Brief description what this tracker is for. Will not be shown on alerts.'
        )
    )
    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.SET_DEFAULT,
        default=None, 
        null=True, 
        blank=True,
        help_text='Webhook URL for a channel on Discord to sent all alerts to'
    )
    origin_solar_system = models.ForeignKey(
        EveSolarSystem, 
        on_delete=models.SET_DEFAULT,
        default=None, 
        null=True, 
        blank=True,
        help_text=(
            'Solar system to calculate ranges and jumps from. '
            '(usually the staging system).'
        )
    )
    exclude_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name='exclude_attacker_alliances_set',
        default=None,
        blank=True,
        help_text='exclude killmails with attackers are from one of these alliances'
    )
    required_attacker_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name='required_attacker_alliances_set',
        default=None,         
        blank=True,
        help_text='only include killmails with attackers from one of these alliances'
    )    
    require_victim_alliances = models.ManyToManyField(
        EveAllianceInfo,
        related_name='require_victim_alliances_set',
        default=None,         
        blank=True,
        help_text=(
            'only include killmails where the victim belongs to one of these alliances'
        )
    )
    identify_fleets = models.BooleanField(
        default=False, 
        help_text='when true: kills are interpreted and shown as fleet kills'
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
            'exclude killmails from high sec.'
            'Also exclude high sec systems in route finder for jumps from origin.'
        )
    )
    exclude_low_sec = models.BooleanField(
        default=False, help_text='exclude killmails from low sec'
    )
    exclude_null_sec = models.BooleanField(
        default=False, help_text='exclude killmails from null sec'
    )
    exclude_w_space = models.BooleanField(
        default=False, help_text='exclude killmails from WH space'
    )
    min_attackers = models.PositiveIntegerField(
        default=None, 
        null=True, 
        blank=True,
        help_text='Require killmails to have at least given amount of attackers'
    )
    max_attackers = models.PositiveIntegerField(
        default=None, 
        null=True, 
        blank=True,
        help_text='Exclude killmails that exceed given amount of attackers'
    )
    max_jumps = models.PositiveIntegerField(
        default=None, 
        null=True, 
        blank=True,
        help_text=(
            'Exclude killmails which are more than x jumps away'
            'from the origin solar system'
        )
    )
    min_value = models.PositiveIntegerField(
        default=None, 
        null=True, 
        blank=True,
        help_text='Exclude killmails with a value below the given amount'
    )
    max_distance = models.FloatField(
        default=None, 
        null=True, 
        blank=True,
        help_text=(
            'Exclude killmails which are farer away from the origin solar system '
            'than the given distance in lightyears'
        )
    )
    require_attackers_ship_groups = models.ManyToManyField(
        EveGroup,
        related_name='require_attackers_ship_groups_set',
        default=None,        
        blank=True,
        help_text=(
            'exclude killmails where attackers are flying one of these ship groups'
        )
    )
    max_age = models.PositiveIntegerField(
        default=DEFAULT_MAX_AGE_HOURS, 
        blank=True, 
        help_text=(
            'ignore killmails that are older than the given number in hours '
            '(sometimes killmails appear belated on ZKB - '
            'this feature ensures they don\'t create new alerts)'
        )
    )
    is_activated = models.BooleanField(
        default=True, help_text='toogle for activating or deactivating a tracker'
    )

    def __str__(self):
        return self.name

    def calculate_killmails(self, force=False):
        """adds all calculated information to killmails for this filter """
        
        # fetch all non processed killmails and add calculated values
        processed_counter = 0
        if force:
            processed_ids = list()
        else:
            processed_ids = TrackerKillmail.objects\
                .filter(is_matching__isnull=False)\
                .values_list('killmail_id', flat=True)        
        for killmail in Killmail.objects.exclude(id__in=processed_ids):
            dest_solar_system = killmail.solar_system.get_pendant_object()
            TrackerKillmail.objects.update_or_create(
                killmail=killmail,
                tracker=self,
                defaults={
                    'is_high_sec': dest_solar_system.is_high_sec,
                    'is_low_sec': dest_solar_system.is_low_sec,
                    'is_null_sec': dest_solar_system.is_null_sec,
                    'is_w_space': dest_solar_system.is_w_space,
                    'distance': meters_to_ly(
                        self.origin_solar_system.distance_to(dest_solar_system)
                    )
                }
            )
            processed_counter += 1
        
        # apply all filters from tracker to determine matching killmails
        matching = TrackerKillmail.objects.filter(is_matching__isnull=True)

        if self.max_age:
            threshold_date = now() - timedelta(hours=self.max_age)
            matching = matching.exclude(killmail__time__lt=threshold_date)

        if self.exclude_high_sec:
            matching = matching.exclude(is_high_sec=True)

        if self.exclude_low_sec:
            matching = matching.exclude(is_low_sec=True)

        if self.exclude_null_sec:
            matching = matching.exclude(is_null_sec=True)

        if self.exclude_w_space:
            matching = matching.exclude(is_w_space=True)

        if self.min_attackers:
            matching = matching.exclude(attackers_count__lt=self.min_attacker)

        if self.max_attackers:
            matching = matching.exclude(attackers_count__gt=self.max_attackers)

        if self.min_value:
            matching = matching.exclude(killmail__zkb__total_value__lt=self.min_value)

        if self.max_distance:
            matching = matching.exclude(distance__gt=self.max_distance)

        if self.exclude_attacker_alliances:
            alliance_ids = self._extract_alliance_ids(self.exclude_attacker_alliances)
            matching = \
                matching.exclude(killmail__attackers__alliance__id__in=alliance_ids)

        if self.required_attacker_alliances:
            alliance_ids = self._extract_alliance_ids(self.required_attacker_alliances)
            matching = \
                matching.filter(killmail__attackers__alliance__id__in=alliance_ids)

        if self.require_victim_alliances:
            alliance_ids = self._extract_alliance_ids(self.require_victim_alliances)
            
        # store which killmails match with the tracker
        for killmail in TrackerKillmail.objects.filter(is_matching__isnull=True):
            if killmail in matching:
                killmail.is_matching = True
            else:
                killmail.is_matching = False
            killmail.save()
        
        return processed_counter

    @staticmethod
    def _extract_alliance_ids(alliances) -> list:
        return [
            int(alliance_id) 
            for alliance_id in alliances.values_list('alliance_id', flat=True)
        ]

    def send_matching_to_webhook(self) -> int:                       
        matching_killmails = TrackerKillmail.objects\
            .filter(tracker=self, date_sent__isnull=True, is_matching=True)\
            .select_related()\
            .order_by('killmail__time')
        killmail_counter = 0
        for matching in matching_killmails:
            logger.debug(
                'Sending killmail with ID %d to webhook', matching.killmail.id
            )
            try:
                matching.killmail.send_to_webhook(self.webhook.url)
            except Exception as ex:
                logger.exception(ex)
                pass
            
            sleep(DISCORD_SEND_DELAY)
            killmail_counter += 1
            if killmail_counter > 10:
                break

        return killmail_counter


class TrackerKillmail(models.Model):

    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE)
    killmail = models.ForeignKey(Killmail, on_delete=models.CASCADE)    
    is_matching = models.BooleanField(default=None, null=True)
    date_sent = models.DateTimeField(default=None, null=True)
    attackers_count = models.PositiveIntegerField(
        default=None, 
        null=True,         
        help_text='Calculated number of attackers'
    )
    jumps = models.PositiveIntegerField(
        default=None, 
        null=True,         
        help_text='Calculated number of jumps from origin'
    )
    distance = models.FloatField(
        default=None, 
        null=True,         
        help_text='Calculated distance from origin in lightyears'
    )
    is_high_sec = models.BooleanField(default=None, null=True)
    is_low_sec = models.BooleanField(default=None, null=True)
    is_null_sec = models.BooleanField(default=None, null=True)
    is_w_space = models.BooleanField(default=None, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tracker', 'killmail'], name='functional_key'
            )
        ]

    def __repr__(self):
        return (
            f'{type(self).__name__}(tracker=\'{self.tracker}\''
            f', killmail_id={self.killmail_id})'
        )
