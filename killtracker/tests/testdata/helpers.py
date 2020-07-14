from datetime import datetime
import json

from allianceauth.eveonline.models import EveAllianceInfo
from eveuniverse.models import EveEntity, EveUniverseEntityModel

from ...models import Killmail
from .load_eveuniverse import load_eveuniverse
from . import _currentdir


def _load_json_from_file(filename: str):
    with open(f"{_currentdir}/{filename}.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def load_eveentities():
    for item in _load_json_from_file("eveentities"):
        EveEntity.objects.update_or_create(
            id=item["id"], defaults={"name": item["name"], "category": item["category"]}
        )

    for MyModel in EveUniverseEntityModel.all_models():
        if MyModel.eve_entity_category():
            for obj in MyModel.objects.all():
                EveEntity.objects.update_or_create(
                    id=obj.id,
                    defaults={
                        "name": obj.name,
                        "category": MyModel.eve_entity_category(),
                    },
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
    load_eveuniverse()
    load_eveentities()
    load_evealliances()
    load_killmails()
