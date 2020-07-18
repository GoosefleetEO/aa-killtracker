from datetime import timedelta
import requests

from django.db import models, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from allianceauth.services.hooks import get_extension_logger

from eveuniverse.helpers import meters_to_ly
from eveuniverse.models import EveEntity

from . import __title__
from .app_settings import KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

ZKB_REDISQ_URL = "https://redisq.zkillboard.com/listen.php"
ZKB_REDISQ_TIMEOUT = (5, 30)

CHARACTER_PROPS = (
    ("character_id", "character"),
    ("corporation_id", "corporation"),
    ("alliance_id", "alliance"),
    ("faction_id", "faction"),
    ("ship_type_id", "ship_type"),
)

MAX_FETCHED_KILLMAILS_PER_RUN = 10


class KillmailQuerySet(models.QuerySet):
    """Custom queryset for Killmail"""

    def load_entities(self) -> int:
        """loads unknown entities for all killmails of this QuerySet. 
        Returns count of updated entities
        """
        entity_ids = []
        for killmail in self:
            entity_ids += killmail.entity_ids()

        return EveEntity.objects.filter(
            id__in=list(set(entity_ids)), name=""
        ).update_from_esi()


class KillmailManager(models.Manager):
    def get_queryset(self):
        return KillmailQuerySet(self.model, using=self._db)

    def fetch_from_zkb(self) -> object:
        """Fetches and returns a killmail from ZKB. 
        
        Returns None if no killmail is received.
        """
        logger.info("Trying to fetch killmail from ZKB...")
        r = requests.get(ZKB_REDISQ_URL, timeout=ZKB_REDISQ_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data:
            logger.debug("data:\n%s", data)
        if data and "package" in data and data["package"]:
            logger.info("Received a killmail from ZKB")
            package_data = data["package"]
            return self._create_from_dict(package_data)
        else:
            logger.info("ZKB killmail queue is empty")
            return None

    def _create_from_dict(self, package_data: dict) -> object:
        from .models import (
            EveEntity,
            KillmailAttacker,
            KillmailPosition,
            KillmailVictim,
            KillmailZkb,
        )

        with transaction.atomic():
            killmail_id = int(package_data["killID"])
            self.filter(id=killmail_id).delete()
            args = {"id": killmail_id}
            if "killmail" in package_data:
                killmail_data = package_data["killmail"]

                if "killmail_time" in killmail_data:
                    args["time"] = parse_datetime(killmail_data["killmail_time"])

                if "solar_system_id" in killmail_data:
                    args["solar_system"], _ = EveEntity.objects.get_or_create(
                        id=killmail_data["solar_system_id"]
                    )

            killmail = self.create(**args)

            if "zkb" in package_data:
                zkb_data = package_data["zkb"]
                args = {"killmail": killmail}
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

                KillmailZkb.objects.create(**args)

            if "killmail" in package_data:
                package_data = package_data["killmail"]
                if "victim" in package_data:
                    victim_data = package_data["victim"]
                    args = {"killmail": killmail}
                    for prop, field in CHARACTER_PROPS:
                        if prop in victim_data:
                            args[field], _ = EveEntity.objects.get_or_create(
                                id=victim_data[prop]
                            )

                    if "damage_taken" in victim_data:
                        args["damage_taken"] = victim_data["damage_taken"]

                    KillmailVictim.objects.create(**args)

                    if "position" in victim_data:
                        position_data = victim_data["position"]
                        args = {"killmail": killmail}
                        for prop in ["x", "y", "z"]:
                            if prop in position_data:
                                args[prop] = position_data[prop]

                        KillmailPosition.objects.create(**args)

                if "attackers" in package_data:
                    for attacker_data in package_data["attackers"]:
                        args = {"killmail": killmail}
                        for prop, field in CHARACTER_PROPS + (
                            ("faction_id", "faction"),
                            ("weapon_type_id", "weapon_type"),
                        ):
                            if prop in attacker_data:
                                args[field], _ = EveEntity.objects.get_or_create(
                                    id=attacker_data[prop]
                                )
                        if "damage_done" in attacker_data:
                            args["damage_done"] = attacker_data["damage_done"]

                        if "security_status" in attacker_data:
                            args["security_status"] = attacker_data["security_status"]

                        if "final_blow" in attacker_data:
                            args["is_final_blow"] = attacker_data["final_blow"]

                        KillmailAttacker.objects.create(**args)

        killmail.refresh_from_db()
        return killmail

    def delete_stale(self):
        """deletes all stale killmail"""
        deadline = now() - timedelta(days=KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS)
        self.filter(time__lt=deadline).delete()


class TrackedKillmailQuerySet(models.QuerySet):
    def killmail_ids(self) -> set:
        return set(self.order_by("killmail_id").values_list("killmail_id", flat=True))


class TrackedKillmailManager(models.Manager):
    def get_queryset(self):
        return TrackedKillmailQuerySet(self.model, using=self._db)

    def generate(self, tracker: object, force: bool = False) -> int:
        """Generate objects for all yet unprocessed killmails
        
        Returns the count of generated objects
        """
        from .models import Killmail

        processed_counter = 0
        if force:
            killmails_qs = Killmail.objects.all()
        else:
            killmails_qs = Killmail.objects.exclude(
                trackedkillmail__tracker=tracker,
                trackedkillmail__is_matching__isnull=False,
            )

        killmails_qs.load_entities()
        for killmail in killmails_qs:
            distance = None
            jumps = None
            is_high_sec = None
            is_low_sec = None
            is_null_sec = None
            is_w_space = None
            solar_system = None
            if killmail.solar_system:
                (
                    solar_system,
                    _,
                ) = killmail.solar_system.get_or_create_pendant_object()
                if solar_system and tracker.origin_solar_system:
                    if tracker.require_max_distance:
                        distance = meters_to_ly(
                            tracker.origin_solar_system.distance_to(solar_system)
                        )
                    if tracker.require_max_jumps:
                        jumps = tracker.origin_solar_system.jumps_to(solar_system)

                if solar_system:
                    is_high_sec = solar_system.is_high_sec
                    is_low_sec = solar_system.is_low_sec
                    is_null_sec = solar_system.is_null_sec
                    is_w_space = solar_system.is_w_space

            (
                victim_ship_type,
                _,
            ) = killmail.victim.ship_type.get_or_create_pendant_object()

            obj, _ = self.update_or_create(
                tracker=tracker,
                killmail=killmail,
                defaults={
                    "is_high_sec": is_high_sec,
                    "is_low_sec": is_low_sec,
                    "is_null_sec": is_null_sec,
                    "is_w_space": is_w_space,
                    "distance": distance,
                    "jumps": jumps,
                    "attackers_count": killmail.attackers.count(),
                    "solar_system": solar_system,
                    "victim_ship_type": victim_ship_type,
                },
            )

            for attacker in killmail.attackers.all():
                if attacker.ship_type:
                    ship_type, _ = attacker.ship_type.get_or_create_pendant_object()
                    if ship_type:
                        obj.attackers_ship_types.add(ship_type)

            processed_counter += 1

        logger.debug("Generated %s objects for tracker %s", processed_counter, tracker)
        return processed_counter
