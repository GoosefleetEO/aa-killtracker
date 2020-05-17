import json
import logging
import urllib

import dhooks_lite

from django.db import models
from django.utils.translation import gettext_lazy as _

from . import __title__
from .managers import EveEntityManager, KillmailManager

logger = logging.getLogger('allianceauth')
WEBHOOK_URL = 'https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO'  # noqa


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
        return f'{type(self).__name__}(id=\'{self.id}\')'

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


class Killmail(models.Model):
    
    ZKB_KILLMAIL_BASEURL = 'https://zkillboard.com/kill/'

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True)
    solar_system = models.ForeignKey(
        EveEntity, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    is_processed = models.BooleanField(default=False)

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
