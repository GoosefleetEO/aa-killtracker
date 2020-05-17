import logging
from time import sleep

import requests

from django.db import models, transaction

from esi.models import esi_client_factory


_client = None
logger = logging.getLogger(__name__)

ZKB_REDISQ_URL = 'https://redisq.zkillboard.com/listen.php'
ZKB_REDISQ_TIMEOUT = 5
ESI_API_TIMEOUT = 5

CHARACTER_PROPS = [
    'character_id', 
    'corporation_id', 
    'alliance_id', 
    'faction_id', 
    'ship_type_id'
]

WEBHOOK_URL = 'https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO'  # noqa

# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
DISCORD_SEND_DELAY = 1


class EveEntityQuerySet(models.QuerySet):

    def id2name(self, id: int) -> str:
        """returns the name for the given ID from the queryset if found
        
        else returns ''
        """
        try:
            entity = self.get(id=id)
        except self.model.DoesNotExist:
            return ''
        else:
            return entity.name


class EveEntityManager(models.Manager):

    def get_queryset(self):
        return EveEntityQuerySet(self.model, using=self._db)

    @staticmethod
    def _get_client():
        global _client
        if _client is None:
            logger.info('Loading ESI client...')
            _client = esi_client_factory()
        return _client
    
    def fetch_entities(self, ids: set) -> list:        
        ids = {int(x) for x in ids}
        ids_found = self.filter(id__in=ids).values_list(flat=True)
        if ids_found:
            ids_found = set(ids_found)
        else:
            ids_found = set()
        
        ids_not_found = ids - ids_found
        if ids_not_found:
            client = self._get_client()
            logger.info('Fetching ids:\n%s', ids_found)
            results = \
                client.Universe.post_universe_names(ids=list(ids_not_found))\
                .result(timeout=ESI_API_TIMEOUT)
            for item in results:
                self.update_or_create(
                    id=item['id'],
                    defaults={
                        'name': item['name'],
                        'category': item['category']
                    }
                )
        return self.filter(id__in=ids)

    def fetch_entity(self, id: str) -> object:
        try:
            entity = self.get(id=id)
        except self.model.DoesNotExist:
            return self.fetch_entities([id]).first()
        else:
            return entity


class KillmailManager(models.Manager):
    
    def fetch_killmails(self):
        from .models import (            
            KillmailAttacker, KillmailPosition, KillmailVictim, KillmailZkb,     
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
                            args['solar_system_id'] = killmail_data['solar_system_id']

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
                            for prop in CHARACTER_PROPS + [
                                'damage_taken'
                            ]:
                                if prop in victim_data:
                                    args[prop] = victim_data[prop]

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
                                for prop in CHARACTER_PROPS + [
                                    'damage_done',                                 
                                    'security_status', 
                                    'faction_id', 
                                    'weapon_type_id'
                                ]:
                                    if prop in attacker_data:
                                        args[prop] = attacker_data[prop]

                                if 'final_blow' in attacker_data:
                                    args['is_final_blow'] = attacker_data['final_blow']

                                KillmailAttacker.objects.create(**args)
                
            else:
                break

        logger.info('Retrieved %s killmail from ZKB', killmail_counter)
            
    def process_killmails(self):                       
        killmails = self.filter(is_processed=False).select_related()
        killmail_counter = 0
        for killmail in killmails:
            killmail.send_to_webhook(WEBHOOK_URL)
            sleep(DISCORD_SEND_DELAY)
            killmail_counter += 1
            if killmail_counter > 10:
                break
