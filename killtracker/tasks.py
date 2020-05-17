import logging
from time import sleep

from celery import shared_task
import requests
from dhooks_lite import Webhook

from django.db import transaction

from .models import (
    EveEntity,
    Killmail, 
    KillmailAttacker,     
    KillmailPosition, 
    KillmailVictim, 
    KillmailZkb,     
)


logger = logging.getLogger(__name__)

ZKB_REDISQ_URL = 'https://redisq.zkillboard.com/listen.php'

CHARACTER_PROPS = [
    'character_id', 
    'corporation_id', 
    'alliance_id', 
    'faction_id', 
    'ship_type_id'
]

WEBHOOK_URL = 'https://discordapp.com/api/webhooks/519251066089373717/MOhnV35wtgIQv8nMy_bD5Eda5EVxcjdm6y3ZmpfFH8nV97i45T_g-xDuoRRo13i-KwIO'  # noqa


@shared_task
def fetch_killmails():
    logger.info('Starting to fetch killmail from ZKB')
    killmail_counter = 0    
    for _ in range(10):
        r = requests.get(ZKB_REDISQ_URL)
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
                Killmail.objects.filter(id=killmail_id).delete()
                args = {'id': killmail_id}
                if 'killmail' in package_data:
                    killmail_data = package_data['killmail']

                    if 'killmail_time' in killmail_data:
                        args['time'] = killmail_data['killmail_time']

                    if 'solar_system_id' in killmail_data:
                        args['solar_system_id'] = killmail_data['solar_system_id']

                killmail = Killmail.objects.create(**args)
                
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


def resolve_name(qs, id) -> str:
    try:
        entity = qs.get(id=id)
    except EveEntity.DoesNotExist:
        return ''
    else:
        return entity.name
    
    
@shared_task
def process_killmails():    
    killmail = Killmail.objects.filter(is_processed=False).select_related()
    killmail_counter = 0
    for killmail in killmail:
        ids = list({
            x for x in [
                killmail.victim.character_id,
                killmail.victim.corporation_id,
                killmail.victim.alliance_id,
                killmail.victim.ship_type_id,
                killmail.solar_system_id
            ]
            if x is not None
        })        
        entities = EveEntity.objects.fetch_entities(ids)
        try:            
            victim_character = resolve_name(entities, killmail.victim.character_id)
            victim_ship = resolve_name(entities, killmail.victim.ship_type_id)
            solar_system = resolve_name(entities, killmail.solar_system_id)
            value_mio = int(killmail.zkb.total_value / 1000000)

            text = (
                f'{victim_character} lost their {victim_ship} in {solar_system} '
                f'worth {value_mio} M ISK.'
            )
            logger.info('Sending killmail to Discord')
            hook = Webhook(url=WEBHOOK_URL)
            hook.execute(content=text, wait_for_response=True)
            sleep(1)
            killmail.is_processed = True
            killmail.save()
            killmail_counter += 1
            if killmail_counter > 10:
                break
            
        except OSError as ex:
            logger.exception(ex)
            pass

        except Exception as ex:
            logger.exception(ex)
            pass
