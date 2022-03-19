from itertools import count

from django.utils.timezone import now

from ...models import EveKillmail, EveKillmailAttacker


def create_eve_killmail(**kwargs):
    params = {
        "id": next_number("eve_killmail"),
        "time": now(),
        "character_id": 1001,
        "corporation_id": 2001,
        "alliance_id": 3001,
        "faction_id": 500001,
        "damage_taken": 10_000_000,
        "position_x": 1,
        "position_y": -1,
        "position_z": 0,
        "is_npc": False,
        "is_solo": True,
        "is_awox": False,
    }
    params.update(default_param(kwargs, "solar_system", 30003069))  # Kamela
    params.update(default_param(kwargs, "ship_type", 603))  # Merlin
    params.update(kwargs)
    killmail = EveKillmail.objects.create(**params)
    return killmail


def create_eve_killmail_attacker(eve_killmail, **kwargs):
    params = {
        "killmail": eve_killmail,
        "character_id": 1011,
        "corporation_id": 2011,
        "alliance_id": 3011,
        "faction_id": 500004,
        "damage_done": 10_000_000,
        "security_status": -10,
    }
    params.update(default_param(kwargs, "ship_type", 34562))  # Svipul
    params.update(default_param(kwargs, "weapon_type", 2977))  # 280mm Arty
    params.update(kwargs)
    return EveKillmailAttacker.objects.create(**params)


def default_param(params: dict, param_name: str, id_value) -> dict:
    param_name_id = f"{param_name}_id"
    if param_name not in params and param_name_id not in params:
        return {param_name_id: id_value}
    return dict()


def next_number(key=None) -> int:
    if key is None:
        key = "_general"
    try:
        return next_number._counter[key].__next__()
    except AttributeError:
        next_number._counter = dict()
    except KeyError:
        pass
    next_number._counter[key] = count(start=1)
    return next_number._counter[key].__next__()
