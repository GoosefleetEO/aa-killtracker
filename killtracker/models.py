import logging
import urllib

from dhooks_lite import Webhook, Embed, Thumbnail, Footer

from django.db import models

from .managers import EveEntityManager, KillmailManager

logger = logging.getLogger(__name__)
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
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES)
    timestamp = models.DateTimeField(auto_now=True, db_index=True)

    objects = EveEntityManager()

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


class Killmail(models.Model):

    EVE_IMAGESERVER_BASE_URL = 'https://images.evetech.net'
    ZKB_KILLMAIL_BASEURL = 'https://zkillboard.com/kill/'

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True)
    solar_system_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    is_processed = models.BooleanField(default=False)

    objects = KillmailManager()

    def __str__(self):
        return str(id)

    def __repr__(self):
        return f'Killmail(id={self.id})'

    @property
    def solar_system_name(self):
        return EveEntity.objects.fetch_entity(self.solar_system_id).name

    @property
    def solar_system_link(self):
        return EveEntity.objects.fetch_entity(self.solar_system_id).zkb_link

    def fetch_entities(self):
        ids = [
            x for x in [
                self.victim.character_id,
                self.victim.corporation_id,
                self.victim.alliance_id,
                self.victim.ship_type_id,
                self.solar_system_id
            ]
            if x is not None
        ]
        for attacker in self.attackers.all():
            ids += [
                x for x in [
                    attacker.character_id,
                    attacker.corporation_id,
                    attacker.alliance_id,
                    attacker.ship_type_id,
                    attacker.weapon_type_id,
                ]
                if x is not None        
            ]
        return EveEntity.objects.fetch_entities(ids)

    def send_to_webhook(self, webhook_url: str = WEBHOOK_URL):
        self.fetch_entities()
        try:                        
            zkb_killmail_url = f'{self.ZKB_KILLMAIL_BASEURL}{self.id}/'            
            value_mio = int(self.zkb.total_value / 1000000)
            attacker = self.attackers.get(is_final_blow=True)
            description = (
                f'{self.victim.character_link} ({self.victim.corporation_link}) '
                f'lost their '
                f'**{self.victim.ship_type_name}** '
                f'in {self.solar_system_link} worth **{value_mio} M** ISK.\n'
                f'Final blow by {attacker.character_link} '
                f'({attacker.corporation_link}) '
                f'in a **{attacker.ship_type_name}**.'
            )
            title = \
                f'{self.solar_system_name} | {self.victim.character_name} | Killmail'
            thumbnail_url = self.type_icon_url(self.victim.ship_type_id)
            footer_text = 'zKillboard'
            embed = Embed(
                description=description,
                title=title,
                url=zkb_killmail_url,
                thumbnail=Thumbnail(url=thumbnail_url),
                footer=Footer(text=footer_text),
                timestamp=self.time
            )            
            logger.info('Sending self to Discord')
            hook = Webhook(url=webhook_url, username='killtracker')
            hook.execute(embeds=[embed], wait_for_response=True)            
            self.is_processed = True
            self.save()
           
        except Exception as ex:
            logger.exception(ex)
            pass

    @classmethod
    def type_icon_url(cls, type_id: int, size: int = 64) -> str:
        if size < 32 or size > 1024 or (size % 2 != 0):
            raise ValueError("Invalid size: {}".format(size))

        url = '{}/types/{}/icon'.format(
            cls.EVE_IMAGESERVER_BASE_URL,
            int(type_id)
        )
        if size:
            args = {'size': int(size)}
            url += '?{}'.format(urllib.parse.urlencode(args))

        return url
    

class KillmailCharacter(models.Model):
    
    character_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    corporation_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    alliance_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    faction_id = models.PositiveIntegerField(default=None, null=True, blank=True)    
    ship_type_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    
    class Meta:
        abstract = True

    @property
    def character_name(self):
        return EveEntity.objects.fetch_entity(self.character_id).name

    @property
    def character_link(self):
        return EveEntity.objects.fetch_entity(self.character_id).zkb_link

    @property
    def corporation_name(self):
        return EveEntity.objects.fetch_entity(self.corporation_id).name

    @property
    def corporation_link(self):
        return EveEntity.objects.fetch_entity(self.corporation_id).zkb_link

    @property
    def alliance_name(self):
        return EveEntity.objects.fetch_entity(self.alliance_id).name

    @property
    def alliance_link(self):
        return EveEntity.objects.fetch_entity(self.alliance_id).zkb_link

    @property
    def faction_name(self):
        return EveEntity.objects.fetch_entity(self.faction_id).name

    @property
    def ship_type_name(self):
        return EveEntity.objects.fetch_entity(self.ship_type_id).name
    
   
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
    weapon_type_id = models.PositiveIntegerField(default=None, null=True, blank=True)

    @property
    def weapon_type_name(self):
        return EveEntity.objects.fetch_entity(self.weapon_type_id).name


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
