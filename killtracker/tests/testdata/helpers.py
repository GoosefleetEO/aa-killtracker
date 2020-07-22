from copy import deepcopy
from datetime import datetime
from hashlib import md5
import json

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo
from eveuniverse.models import EveEntity, EveUniverseEntityModel

from . import _currentdir
from .load_eveuniverse import load_eveuniverse  # noqa
from ...core.killmails import Killmail
from ...models import EveKillmail


def _load_json_from_file(filename: str) -> dict:
    with open(f"{_currentdir}/{filename}.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def _load_killmails_data() -> dict:
    data = dict()
    for obj in _load_json_from_file("killmails"):
        killmail_id = obj["killID"]
        obj["killmail"]["killmail_id"] = killmail_id
        obj["killmail"]["killmail_time"] = datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        my_hash = md5(str(killmail_id).encode("utf8")).hexdigest()
        obj["zkb"]["hash"] = my_hash
        obj["zkb"][
            "href"
        ] = f"https://esi.evetech.net/v1/killmails/{killmail_id}/{my_hash}/"
        data[killmail_id] = obj

    return data


_killmails_data = _load_killmails_data()
_eveentities_data = _load_json_from_file("eveentities")
_evealliances_data = _load_json_from_file("evealliances")
_evecorporations_data = _load_json_from_file("evecorporations")


def killmails_data() -> dict:
    return deepcopy(_killmails_data)


def load_eveentities() -> None:
    for item in _eveentities_data:
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


def load_evealliances() -> None:
    EveAllianceInfo.objects.all().delete()
    for item in _evealliances_data:
        alliance = EveAllianceInfo.objects.create(**item)
        EveEntity.objects.create(
            id=alliance.alliance_id,
            name=alliance.alliance_name,
            category=EveEntity.CATEGORY_ALLIANCE,
        )


def load_evecorporations() -> None:
    EveCorporationInfo.objects.all().delete()
    for item in _evecorporations_data:
        corporation = EveCorporationInfo.objects.create(**item)
        EveEntity.objects.create(
            id=corporation.corporation_id,
            name=corporation.corporation_name,
            category=EveEntity.CATEGORY_CORPORATION,
        )


def load_eve_killmails(killmail_ids: set = None) -> None:
    if killmail_ids:
        killmail_ids = set(killmail_ids)
    EveKillmail.objects.all().delete()
    for killmail_id, item in _killmails_data.items():
        if not killmail_ids or killmail_id in killmail_ids:
            killmail = Killmail._create_from_dict(item)
            EveKillmail.objects.create_from_killmail(killmail)


def load_killmail(killmail_id: int) -> Killmail:
    for item_id, item in _killmails_data.items():
        if killmail_id == item_id:
            return Killmail._create_from_dict(item)

    raise ValueError(f"Killmail with id {killmail_id} not found.")
