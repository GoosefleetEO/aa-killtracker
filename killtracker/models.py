from django.db import models

from .managers import EveEntityManager


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        managed = False                         
        default_permissions = ()
        permissions = ( 
            ('basic_access', 'Can access this app'), 
        )


class Killmail(models.Model):

    id = models.BigIntegerField(primary_key=True)
    time = models.DateTimeField(default=None, null=True, blank=True)
    solar_system_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    is_processed = models.BooleanField(default=False)

    def __str__(self):
        return str(id)

    def __repr__(self):
        return f'Killmail(id={self.id})'
    

class KillmailCharacter(models.Model):
    
    character_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    corporation_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    alliance_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    faction_id = models.PositiveIntegerField(default=None, null=True, blank=True)    
    ship_type_id = models.PositiveIntegerField(default=None, null=True, blank=True)
    
    class Meta:
        abstract = True


class KillmailVictim(KillmailCharacter):

    killmail = models.OneToOneField(
        Killmail, primary_key=True, on_delete=models.CASCADE, related_name='victim'
    )
    damage_taken = models.BigIntegerField(default=None, null=True, blank=True)
    

class KillmailAttacker(KillmailCharacter):

    killmail = models.ForeignKey(
        Killmail, on_delete=models.CASCADE, related_name='attacker'
    )
    damage_done = models.BigIntegerField(default=None, null=True, blank=True)
    is_final_blow = models.BooleanField(default=None, null=True, blank=True)
    security_status = models.FloatField(default=None, null=True, blank=True)
    weapon_type_id = models.PositiveIntegerField(default=None, null=True, blank=True)


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


class EveEntity(models.Model):
    
    CATEGORY_ALLIANCE = 'alliance'
    CATEGORY_CHARACTER = 'character'
    CATEGORY_CONSTELLATION = 'constellation'
    CATEGORY_CORPORATION = 'corporation'
    CATEGORY_FACTIONS = 'factions'
    CATEGORY_REGIONS = 'regions'    
    CATEGORY_SOLAR_SYSTEM = 'solar_system'
    CATEGORY_STATION = 'station'
    CATEGORY_INVENTORY_TYPE = 'inventory_type'

    CATEGORY_CHOICES = (
        (CATEGORY_ALLIANCE, 'alliance'),
        (CATEGORY_CHARACTER, 'character'),
        (CATEGORY_CONSTELLATION, 'constellation'),
        (CATEGORY_CORPORATION, 'corporation'),
        (CATEGORY_FACTIONS, 'factions'),
        (CATEGORY_REGIONS, 'regions'),
        (CATEGORY_SOLAR_SYSTEM, 'solar_system'),
        (CATEGORY_STATION, 'station'),
        (CATEGORY_INVENTORY_TYPE, 'inventory_type'),
    )
    
    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES)
    timestamp = models.DateField(auto_now=True, db_index=True)

    objects = EveEntityManager()

    def __repr__(self):
        return f'{type(self).__name__}(id=\'{self.id}\')'