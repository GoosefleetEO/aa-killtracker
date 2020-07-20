from datetime import datetime
from dataclasses import dataclass, asdict
import json
from typing import List, Optional

from dacite import from_dict, DaciteError
import requests

from django.utils.dateparse import parse_datetime

from allianceauth.services.hooks import get_extension_logger

from .. import __title__
from .json_serializer import JsonDateTimeDecoder, JsonDateTimeEncoder
from ..providers import esi
from ..utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

ZKB_REDISQ_URL = "https://redisq.zkillboard.com/listen.php"
ZKB_API_URL = "https://zkillboard.com/api/"
REQUESTS_TIMEOUT = (5, 30)

CHARACTER_PROPS = [
    "character_id",
    "corporation_id",
    "alliance_id",
    "faction_id",
    "ship_type_id",
]


@dataclass
class _KillmailCharacter:
    character_id: Optional[int] = None
    corporation_id: Optional[int] = None
    alliance_id: Optional[int] = None
    faction_id: Optional[int] = None
    ship_type_id: Optional[int] = None


@dataclass
class KillmailVictim(_KillmailCharacter):
    damage_taken: Optional[int] = None


@dataclass
class KillmailAttacker(_KillmailCharacter):
    damage_done: Optional[int] = None
    is_final_blow: Optional[bool] = None
    security_status: Optional[float] = None
    weapon_type_id: Optional[int] = None


@dataclass
class KillmailPosition:
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


@dataclass
class KillmailZkb:
    location_id: Optional[int] = None
    hash: Optional[str] = None
    fitted_value: Optional[float] = None
    total_value: Optional[float] = None
    points: Optional[int] = None
    is_npc: Optional[bool] = None
    is_solo: Optional[bool] = None
    is_awox: Optional[bool] = None


@dataclass
class TrackerInfo:
    tracker_pk: int
    jumps: Optional[int] = None
    distance: Optional[float] = None


@dataclass
class Killmail:
    id: int
    time: datetime
    victim: KillmailVictim
    attackers: List[KillmailAttacker]
    position: KillmailPosition
    zkb: KillmailZkb
    solar_system_id: Optional[int] = None
    tracker_info: Optional[TrackerInfo] = None

    def entity_ids(self) -> set:
        ids = [
            self.victim.character_id,
            self.victim.corporation_id,
            self.victim.alliance_id,
            self.victim.faction_id,
            self.victim.ship_type_id,
            self.solar_system_id,
        ]
        for attacker in self.attackers:
            ids += [
                attacker.character_id,
                attacker.corporation_id,
                attacker.alliance_id,
                attacker.faction_id,
                attacker.ship_type_id,
                attacker.weapon_type_id,
            ]
        return {x for x in ids if x is not None}

    def asdict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> object:
        try:
            return from_dict(data_class=Killmail, data=data)
        except DaciteError as ex:
            logger.error("Failed to convert dict to %s", type(cls), exc_info=True)
            raise ex

    def asjson(self) -> str:
        return json.dumps(asdict(self), cls=JsonDateTimeEncoder)

    @classmethod
    def from_json(cls, json_str: str) -> object:
        return cls.from_dict(json.loads(json_str, cls=JsonDateTimeDecoder))

    @classmethod
    def create_from_zkb_redisq(cls) -> object:
        """Fetches and returns a killmail from ZKB. 
        
        Returns None if no killmail is received.
        """
        logger.info("Trying to fetch killmail from ZKB RedisQ...")
        r = requests.get(ZKB_REDISQ_URL, timeout=REQUESTS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data:
            logger.debug("data:\n%s", data)
        if data and "package" in data and data["package"]:
            logger.info("Received a killmail from ZKB")
            package_data = data["package"]
            return cls._create_from_dict(package_data)
        else:
            logger.info("ZKB killmail queue is empty")
            return None

    @classmethod
    def create_from_zkb_api(cls, killmail_id: int) -> "Killmail":
        """Fetches and returns a killmail from ZKB API. """

        logger.info(
            "Trying to fetch killmail from ZKB API with killmail ID %d ...", killmail_id
        )
        url = f"{ZKB_API_URL}killID/{killmail_id}/"
        r = requests.get(url, timeout=REQUESTS_TIMEOUT)
        r.raise_for_status()
        zkb_data = r.json()
        if not zkb_data:
            logger.warning(
                "ZKB API did not return any data for killmail ID %d", killmail_id
            )
            return None

        logger.debug("data:\n%s", zkb_data)
        try:
            killmail_zkb = zkb_data[0]
        except KeyError:
            return None

        killmail_esi = esi.client.Killmails.get_killmails_killmail_id_killmail_hash(
            killmail_id=killmail_id, killmail_hash=killmail_zkb["zkb"]["hash"]
        ).results()
        if not killmail_esi:
            logger.warning(
                "ESI did not return any data for killmail ID %d", killmail_id
            )
            return None

        # esi returns datetime, but _create_from_dict() expects a string
        # like returned from zkb redisq
        killmail_esi["killmail_time"] = killmail_esi["killmail_time"].isoformat()

        killmail_dict = {
            "killID": killmail_id,
            "killmail": killmail_esi,
            "zkb": killmail_zkb["zkb"],
        }
        return cls._create_from_dict(killmail_dict)

    @staticmethod
    def _create_from_dict(package_data: dict) -> object:
        zkb = KillmailZkb()
        if "zkb" in package_data:
            zkb_data = package_data["zkb"]
            args = {}
            for prop, mapping in (
                ("locationID", "location_id"),
                ("hash", None),
                ("fittedValue", "fitted_value"),
                ("totalValue", "total_value"),
                ("points", None),
                ("npc", "is_npc"),
                ("solo", "is_solo"),
                ("awox", "is_awox"),
            ):
                if prop in zkb_data:
                    if mapping:
                        args[mapping] = zkb_data[prop]
                    else:
                        args[prop] = zkb_data[prop]

            zkb = KillmailZkb(**args)

        killmail = None
        if "killmail" in package_data:
            victim = KillmailVictim()
            position = KillmailPosition()
            attackers = list()
            killmail_data = package_data["killmail"]
            if "victim" in killmail_data:
                victim_data = killmail_data["victim"]
                args = dict()
                for prop in CHARACTER_PROPS + ["damage_taken"]:
                    if prop in victim_data:
                        args[prop] = victim_data[prop]

                victim = KillmailVictim(**args)

                if "position" in victim_data:
                    position_data = victim_data["position"]
                    args = dict()
                    for prop in ["x", "y", "z"]:
                        if prop in position_data:
                            args[prop] = position_data[prop]

                    position = KillmailPosition(**args)

            if "attackers" in killmail_data:
                for attacker_data in killmail_data["attackers"]:
                    args = dict()
                    for prop in CHARACTER_PROPS + [
                        "weapon_type_id",
                        "damage_done",
                        "security_status",
                    ]:
                        if prop in attacker_data:
                            args[prop] = attacker_data[prop]

                    if "final_blow" in attacker_data:
                        args["is_final_blow"] = attacker_data["final_blow"]

                    attackers.append(KillmailAttacker(**args))

            args = {
                "id": killmail_data["killmail_id"],
                "time": parse_datetime(killmail_data["killmail_time"]),
                "victim": victim,
                "position": position,
                "attackers": attackers,
                "zkb": zkb,
            }
            if "solar_system_id" in killmail_data:
                args["solar_system_id"] = killmail_data["solar_system_id"]

            killmail = Killmail(**args)

        return killmail
