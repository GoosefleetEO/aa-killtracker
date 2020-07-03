from datetime import datetime

import inspect
import json
import os

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveRegion, EveSolarSystem, EveGroup, EveType

from ...models import EveEntity, Killmail

_currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))


def _load_json_from_file(filename: str):
    with open(f"{_currentdir}/{filename}.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def load_evesde():
    data = _load_json_from_file("evesde")
    sde_models = [EveGroup, EveType, EveRegion, EveSolarSystem]
    for SdeModel in sde_models:
        SdeModel.objects.all().delete()
    for SdeModel in sde_models:
        for item in data[SdeModel.__name__]:
            obj = SdeModel.objects.create(**item)
            if SdeModel == EveType:
                EveEntity.objects.create(
                    id=obj.id,
                    name=obj.name,
                    category=EveEntity.CATEGORY_INVENTORY_TYPE,
                )
            elif SdeModel == EveSolarSystem:
                EveEntity.objects.create(
                    id=obj.id, name=obj.name, category=EveEntity.CATEGORY_SOLAR_SYSTEM,
                )


def load_eveentities():
    for item in _load_json_from_file("eveentities"):
        EveEntity.objects.update_or_create(
            id=item["id"], defaults={"name": item["name"], "category": item["category"]}
        )


def load_evealliances():
    EveAllianceInfo.objects.all().delete()
    for item in _load_json_from_file("evealliances"):
        alliance = EveAllianceInfo.objects.create(**item)
        EveEntity.objects.create(
            id=alliance.alliance_id,
            name=alliance.alliance_name,
            category=EveEntity.CATEGORY_ALLIANCE,
        )


def load_killmails(killmail_ids: set = None):
    if killmail_ids:
        killmail_ids = set(killmail_ids)
    Killmail.objects.all().delete()
    for item in _load_json_from_file("killmails"):
        killmail_id = item["killID"]
        if not killmail_ids or killmail_id in killmail_ids:
            item["killmail"]["killmail_id"] = killmail_id
            item["killmail"]["killmail_time"] = datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            my_hash = hash(killmail_id)
            item["zkb"]["hash"] = my_hash
            item["zkb"][
                "href"
            ] = f"https://esi.evetech.net/v1/killmails/{killmail_id}/{my_hash}/"
            Killmail.objects.create_from_dict(item)


def load_all():
    EveEntity.objects.all().delete()
    load_evesde()
    load_eveentities()
    load_evealliances()
    load_killmails()
