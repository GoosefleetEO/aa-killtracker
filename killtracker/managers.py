import logging

import requests

from django.db import models, transaction
from django.utils.dateparse import parse_datetime

from eveuniverse.models import EveEntity

logger = logging.getLogger("allianceauth")

ZKB_REDISQ_URL = "https://redisq.zkillboard.com/listen.php"
ZKB_REDISQ_TIMEOUT = 30

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

    def fetch_from_zkb(self, max_killmails: int = MAX_FETCHED_KILLMAILS_PER_RUN) -> int:
        killmail_counter = 0
        max_killmails = max(1, int(max_killmails))
        for _ in range(max_killmails):
            logger.info("Trying to fetch killmail from ZKB...")
            r = requests.get(ZKB_REDISQ_URL, timeout=ZKB_REDISQ_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if data:
                logger.debug("data:\n%s", data)
            if data and "package" in data and data["package"]:
                logger.info("Received a killmail from ZKB")
                killmail_counter += 1
                package_data = data["package"]
                self.create_from_dict(package_data)
            else:
                break

        logger.info("Retrieved %s killmail from ZKB", killmail_counter)
        return killmail_counter

    def create_from_dict(self, package_data: dict) -> object:
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
                            args["is_final_blow"] = attacker_data["security_status"]

                        if "final_blow" in attacker_data:
                            args["is_final_blow"] = attacker_data["final_blow"]

                        KillmailAttacker.objects.create(**args)

        killmail.refresh_from_db()
        return killmail


class TrackerKillmailQuerySet(models.QuerySet):
    def killmail_ids(self) -> list:
        return list(self.values_list("killmail__id", flat=True))


class TrackerKillmailManager(models.Manager):
    def get_queryset(self):
        return TrackerKillmailQuerySet(self.model, using=self._db)
