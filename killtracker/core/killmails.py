import json
from dataclasses import asdict, dataclass
from datetime import datetime
from http import HTTPStatus
from typing import List, Optional, Set

import requests
from dacite import DaciteError, from_dict
from redis.exceptions import LockError
from simplejson.errors import JSONDecodeError

from django.conf import settings
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from allianceauth.services.hooks import get_extension_logger
from app_utils.allianceauth import get_redis_client
from app_utils.json import JSONDateTimeDecoder, JSONDateTimeEncoder
from app_utils.logging import LoggerAddTag

from .. import USER_AGENT_TEXT, __title__
from ..app_settings import (
    KILLTRACKER_REDISQ_LOCK_TIMEOUT,
    KILLTRACKER_REDISQ_TTW,
    KILLTRACKER_STORAGE_KILLMAILS_LIFETIME,
)
from ..exceptions import KillmailDoesNotExist
from ..providers import esi

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

ZKB_REDISQ_URL = "https://redisq.zkillboard.com/listen.php"
ZKB_API_URL = "https://zkillboard.com/api/"
ZKB_KILLMAIL_BASEURL = "https://zkillboard.com/kill/"
REQUESTS_TIMEOUT = (5, 30)


@dataclass
class _KillmailBase:
    def asdict(self) -> dict:
        return asdict(self)


@dataclass
class _KillmailCharacter(_KillmailBase):
    ENTITY_PROPS = [
        "character_id",
        "corporation_id",
        "alliance_id",
        "faction_id",
        "ship_type_id",
    ]

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
    ENTITY_PROPS = _KillmailCharacter.ENTITY_PROPS + ["weapon_type_id"]

    damage_done: Optional[int] = None
    is_final_blow: Optional[bool] = None
    security_status: Optional[float] = None
    weapon_type_id: Optional[int] = None


@dataclass
class KillmailPosition(_KillmailBase):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


@dataclass
class KillmailZkb(_KillmailBase):
    location_id: Optional[int] = None
    hash: Optional[str] = None
    fitted_value: Optional[float] = None
    total_value: Optional[float] = None
    points: Optional[int] = None
    is_npc: Optional[bool] = None
    is_solo: Optional[bool] = None
    is_awox: Optional[bool] = None


@dataclass(eq=True, frozen=True)
class EntityCount:

    CATEGORY_ALLIANCE = "alliance"
    CATEGORY_CORPORATION = "corporation"
    CATEGORY_INVENTORY_GROUP = "inventory_group"

    id: int
    category: str
    name: Optional[str] = None
    count: Optional[int] = None

    @property
    def is_alliance(self) -> bool:
        return self.category == self.CATEGORY_ALLIANCE

    @property
    def is_corporation(self) -> bool:
        return self.category == self.CATEGORY_CORPORATION


@dataclass
class TrackerInfo(_KillmailBase):
    tracker_pk: int
    jumps: Optional[int] = None
    distance: Optional[float] = None
    main_org: Optional[EntityCount] = None
    main_ship_group: Optional[EntityCount] = None
    matching_ship_type_ids: Optional[List[int]] = None


@dataclass
class Killmail(_KillmailBase):
    _STORAGE_BASE_KEY = "killtracker_storage_killmail_"

    id: int
    time: datetime
    victim: KillmailVictim
    attackers: List[KillmailAttacker]
    position: KillmailPosition
    zkb: KillmailZkb
    solar_system_id: Optional[int] = None
    tracker_info: Optional[TrackerInfo] = None

    def __repr__(self):
        return f"{type(self).__name__}(id={self.id})"

    def attackers_distinct_alliance_ids(self) -> Set[int]:
        """Return distinct alliance IDs of all attackers."""
        return {obj.alliance_id for obj in self.attackers if obj.alliance_id}

    def attackers_distinct_corporation_ids(self) -> Set[int]:
        """Return distinct corporation IDs of all attackers."""
        return {obj.corporation_id for obj in self.attackers if obj.corporation_id}

    def attackers_distinct_character_ids(self) -> Set[int]:
        """Return distinct character IDs of all attackers."""
        return {obj.character_id for obj in self.attackers if obj.character_id}

    def attackers_ship_type_ids(self) -> List[int]:
        """Returns ship type IDs of all attackers with duplicates."""
        return [obj.ship_type_id for obj in self.attackers if obj.ship_type_id]

    def entity_ids(self) -> Set[int]:
        """Return distinct IDs of all entities (excluding None)."""
        ids = {
            self.victim.character_id,
            self.victim.corporation_id,
            self.victim.alliance_id,
            self.victim.faction_id,
            self.victim.ship_type_id,
            self.solar_system_id,
        }
        for attacker in self.attackers:
            ids.update(
                {
                    attacker.character_id,
                    attacker.corporation_id,
                    attacker.alliance_id,
                    attacker.faction_id,
                    attacker.ship_type_id,
                    attacker.weapon_type_id,
                }
            )
        ids.discard(None)
        return ids

    def ship_type_distinct_ids(self) -> Set[int]:
        """Return distinct ship type IDs of all entities that are not None."""
        ids = set(self.attackers_ship_type_ids())
        ids.add(self.victim.ship_type_id)
        return ids

    def attacker_final_blow(self) -> Optional[KillmailAttacker]:
        """Returns the attacker with the final blow or None if not found."""
        for attacker in self.attackers:
            if attacker.is_final_blow:
                return attacker
        return None

    def asjson(self) -> str:
        return json.dumps(asdict(self), cls=JSONDateTimeEncoder)

    def save(self) -> None:
        """Save this killmail to temporary storage."""
        cache.set(
            key=self._storage_key(self.id),
            value=self.asjson(),
            timeout=KILLTRACKER_STORAGE_KILLMAILS_LIFETIME,
        )

    @classmethod
    def get(cls, id: int) -> "Killmail":
        """Fetch a killmail from temporary storage."""
        data = cache.get(key=cls._storage_key(id))
        if not data:
            raise KillmailDoesNotExist(
                f"Killmail with ID {id} does not exist in storage."
            )
        return cls.from_json(data)

    @classmethod
    def _storage_key(cls, id: int) -> str:
        return cls._STORAGE_BASE_KEY + str(id)

    @classmethod
    def from_dict(cls, data: dict) -> "Killmail":
        try:
            return from_dict(data_class=Killmail, data=data)
        except DaciteError as ex:
            logger.error("Failed to convert dict to %s", type(cls), exc_info=True)
            raise ex

    @classmethod
    def from_json(cls, json_str: str) -> "Killmail":
        return cls.from_dict(json.loads(json_str, cls=JSONDateTimeDecoder))

    @classmethod
    def create_from_zkb_redisq(cls) -> Optional["Killmail"]:
        """Fetches and returns a killmail from ZKB.

        Returns None if no killmail is received.
        """
        logger.info("Trying to fetch killmail from ZKB RedisQ...")
        redis = get_redis_client()
        try:
            with redis.lock(
                cls.lock_key(), blocking_timeout=KILLTRACKER_REDISQ_LOCK_TIMEOUT
            ):
                r = requests.get(
                    ZKB_REDISQ_URL,
                    params={"ttw": KILLTRACKER_REDISQ_TTW},
                    timeout=REQUESTS_TIMEOUT,
                    headers={"User-Agent": USER_AGENT_TEXT},
                )
        except LockError:
            logger.warning(
                "Failed to acquire lock for atomic access to RedisQ.",
                exc_info=settings.DEBUG,  # provide details in DEBUG mode
            )
            return None

        if r.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            logger.error("429 Client Error: Too many requests: %s", r.text)
            return None
        r.raise_for_status()
        try:
            data = r.json()
        except JSONDecodeError:
            logger.error("Error from ZKB API:\n%s", r.text)
            return None
        if data:
            logger.debug("data:\n%s", data)
        if data and "package" in data and data["package"]:
            logger.info("Received a killmail from ZKB RedisQ")
            package_data = data["package"]
            return cls._create_from_dict(package_data)
        else:
            logger.debug("Did not received a killmail from ZKB RedisQ")
            return None

    @classmethod
    def create_from_zkb_api(cls, killmail_id: int) -> Optional["Killmail"]:
        """Fetches and returns a killmail from ZKB API.

        results are cached
        """
        cache_key = f"{__title__.upper()}_KILLMAIL_{killmail_id}"
        killmail_json = cache.get(cache_key)
        if killmail_json:
            return Killmail.from_json(killmail_json)

        logger.info(
            "Trying to fetch killmail from ZKB API with killmail ID %d ...",
            killmail_id,
        )
        url = f"{ZKB_API_URL}killID/{killmail_id}/"
        r = requests.get(
            url, timeout=REQUESTS_TIMEOUT, headers={"User-Agent": USER_AGENT_TEXT}
        )
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

        # esi returns datetime, but _create_from_dict() expects a string in
        # same format as returned from zkb redisq
        killmail_esi["killmail_time"] = killmail_esi["killmail_time"].strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        killmail_dict = {
            "killID": killmail_id,
            "killmail": killmail_esi,
            "zkb": killmail_zkb["zkb"],
        }
        killmail = cls._create_from_dict(killmail_dict)
        cache.set(key=cache_key, value=killmail.asjson())
        return killmail

    @staticmethod
    def _create_from_dict(package_data: dict) -> "Killmail":
        """creates a new object from given dict.
        Needs to confirm with data structure returned from ZKB RedisQ
        """
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
                for prop in KillmailVictim.ENTITY_PROPS + ["damage_taken"]:
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
                    for prop in KillmailAttacker.ENTITY_PROPS + [
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

    @staticmethod
    def lock_key() -> str:
        """Key used for lock operation on Redis."""
        return f"{__title__.upper()}_REDISQ_LOCK"

    @classmethod
    def reset_lock_key(cls):
        """Delete lock key if it exists.

        It can happen that a lock key is not cleaned up
        and then prevents this class from ever acquiring a lock again.
        To prevent this we are deleting the lock key at system start.
        """
        redis = get_redis_client()
        if redis.delete(cls.lock_key()) > 0:
            logger.warning("A stuck lock key was cleared.")
