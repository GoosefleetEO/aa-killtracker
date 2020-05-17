import logging
from time import sleep

import requests

from django.db import models, transaction
from django.utils.timezone import now

from .helpers.esi_fetch import esi_fetch
from .utils import chunks


logger = logging.getLogger('allianceauth')

ZKB_REDISQ_URL = 'https://redisq.zkillboard.com/listen.php'
ZKB_REDISQ_TIMEOUT = 30

CHARACTER_PROPS = (
    ('character_id', 'character'),
    ('corporation_id', 'corporation'),
    ('alliance_id', 'alliance'),
    ('faction_id', 'faction'),
    ('ship_type_id', 'ship_type'),
)

WEBHOOK_URL = 'https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO'  # noqa

# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
DISCORD_SEND_DELAY = 1


class EveEntityQuerySet(models.QuerySet):

    def update_from_esi(self) -> int:
        ids = list(self.values_list('id', flat=True))
        if not ids:
            return 0
        else:            
            logger.info('Updating %d entities from ESI', len(ids))
            item_counter = 0
            for chunk_ids in chunks(ids, 1000):
                logger.debug(
                    'Trying to resolve the following IDs from ESI:\n%s', chunk_ids
                )
                items = esi_fetch(
                    'Universe.post_universe_names', args={'ids': chunk_ids}
                )                 
                for item in items:
                    self.update_or_create(
                        id=item['id'],
                        defaults={
                            'name': item['name'],
                            'category': item['category'],
                            'last_updated': now()
                        }
                    )
                item_counter += len(items)
            return item_counter


class EveEntityManager(models.Manager):

    def get_queryset(self):
        return EveEntityQuerySet(self.model, using=self._db)

    def update_all_from_esi(self) -> int:  
        to_be_updated = self.filter(name='')
        if to_be_updated:
            return to_be_updated.update_from_esi()            
        else:
            return 0

    def fetch_entity(self, id: str) -> object:
        try:
            entity = self.get(id=id)
        except self.model.DoesNotExist:
            return self.fetch_entities([id]).first()
        else:
            return entity


class KillmailManager(models.Manager):
    
    def fetch_from_zkb(self) -> int:
        from .models import (            
            EveEntity, KillmailAttacker, KillmailPosition, KillmailVictim, KillmailZkb,    
        )
        logger.info('Starting to fetch killmail from ZKB')
        killmail_counter = 0    
        for _ in range(10):
            r = requests.get(ZKB_REDISQ_URL, timeout=ZKB_REDISQ_TIMEOUT)
            r.raise_for_status()
            data = r.json()    
            if data:
                logger.debug('data:\n%s', data)
            if data and 'package' in data and data['package']:
                logger.info('Received a killmail from ZKB')
                killmail_counter += 1            
                package_data = data['package']        
                with transaction.atomic():
                    killmail_id = int(package_data['killID'])
                    self.filter(id=killmail_id).delete()
                    args = {'id': killmail_id}
                    if 'killmail' in package_data:
                        killmail_data = package_data['killmail']

                        if 'killmail_time' in killmail_data:
                            args['time'] = killmail_data['killmail_time']

                        if 'solar_system_id' in killmail_data:
                            args['solar_system'], _ = EveEntity.objects.get_or_create(
                                id=killmail_data['solar_system_id']
                            )

                    killmail = self.create(**args)
                    
                    if 'zkb' in package_data:
                        zkb_data = package_data['zkb']
                        args = {'killmail': killmail}
                        for prop, mapping in (
                            ('locationID', 'location_id'),
                            ('hash', None),
                            ('fittedValue', 'fitted_value'),
                            ('totalValue', 'total_value'),
                            ('points', None),
                            ('npc', 'is_npc'),
                            ('solo', 'is_solo'),
                            ('awox', 'is_awox'),
                        ):
                            if prop in zkb_data:                            
                                if mapping:
                                    args[mapping] = zkb_data[prop]
                                else:
                                    args[prop] = zkb_data[prop]

                        KillmailZkb.objects.create(**args)
                    
                    if 'killmail' in package_data:
                        killmail_data = package_data['killmail']
                        if 'victim' in killmail_data:
                            victim_data = killmail_data['victim']
                            args = {'killmail': killmail}
                            for prop, field in CHARACTER_PROPS:
                                if prop in victim_data:
                                    args[field], _ = EveEntity.objects.get_or_create(
                                        id=victim_data[prop]
                                    )
                            
                            if 'damage_taken' in victim_data:
                                args['damage_taken'] = victim_data['damage_taken']

                            KillmailVictim.objects.create(**args)
                            
                            if 'position' in victim_data:
                                position_data = victim_data['position']
                                args = {'killmail': killmail}
                                for prop in ['x', 'y', 'z']:
                                    if prop in position_data:
                                        args[prop] = position_data[prop]

                                KillmailPosition.objects.create(**args)
                            
                        if 'attackers' in killmail_data:
                            for attacker_data in killmail_data['attackers']:
                                args = {'killmail': killmail}
                                for prop, field in CHARACTER_PROPS + (
                                    ('faction_id', 'faction'),
                                    ('weapon_type_id', 'weapon_type'),
                                ):
                                    if prop in attacker_data:
                                        args[field], _ = \
                                            EveEntity.objects.get_or_create(
                                                id=attacker_data[prop]
                                        )
                                if 'damage_done' in attacker_data:
                                    args['damage_done'] = attacker_data['damage_done']

                                if 'security_status' in attacker_data:
                                    args['is_final_blow'] = \
                                        attacker_data['security_status']

                                if 'final_blow' in attacker_data:
                                    args['is_final_blow'] = attacker_data['final_blow']

                                KillmailAttacker.objects.create(**args)
                
            else:
                break

        EveEntity.objects.update_all_from_esi()
        logger.info('Retrieved %s killmail from ZKB', killmail_counter)
        return killmail_counter
            
    def process_killmails(self) -> int:                       
        killmails = self.filter(is_processed=False).select_related()
        killmail_counter = 0
        for killmail in killmails:
            logger.debug('Processing killmail with ID %d', killmail.id)
            try:
                killmail.send_to_webhook(WEBHOOK_URL)
            except Exception as ex:
                logger.exception(ex)
                pass
            
            sleep(DISCORD_SEND_DELAY)
            killmail_counter += 1
            if killmail_counter > 10:
                break

        return killmail_counter
