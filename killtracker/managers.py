from datetime import timedelta
from typing import Dict, Tuple

from django.db import models, transaction
from django.utils.timezone import now

from allianceauth.services.hooks import get_extension_logger

from eveuniverse.models import EveEntity

from . import __title__
from .app_settings import KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS
from .core.killmails import Killmail, _KillmailCharacter
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


class EveKillmailQuerySet(models.QuerySet):
    """Custom queryset for EveKillmail"""

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


class EveKillmailManager(models.Manager):
    def get_queryset(self) -> models.QuerySet:
        return EveKillmailQuerySet(self.model, using=self._db)

    def delete_stale(self) -> Tuple[int, Dict[str, int]]:
        """deletes all stale killmail"""
        if KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS > 0:
            deadline = now() - timedelta(days=KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS)
            return self.filter(time__lt=deadline).delete()

    def create_from_killmail(self, killmail: Killmail) -> models.Model:
        """create a new EveKillmail from a Killmail object and returns it"""
        from .models import (
            EveKillmailAttacker,
            EveKillmailPosition,
            EveKillmailVictim,
            EveKillmailZkb,
        )

        with transaction.atomic():
            args = {
                "id": killmail.id,
                "time": killmail.time,
            }
            if killmail.solar_system_id:
                args["solar_system"], _ = EveEntity.objects.get_or_create(
                    id=killmail.solar_system_id
                )
            eve_killmail = self.create(**args)

            if killmail.zkb:
                args = {**killmail.zkb.asdict(), **{"killmail": eve_killmail}}
                EveKillmailZkb.objects.create(**args)

            args = {
                **{
                    "killmail": eve_killmail,
                    "damage_taken": killmail.victim.damage_taken,
                },
                **self._create_args_for_entities(killmail.victim),
            }
            EveKillmailVictim.objects.create(**args)

            args = {**killmail.position.asdict(), **{"killmail": eve_killmail}}
            EveKillmailPosition.objects.create(**args)

            for attacker in killmail.attackers:
                args = {
                    **{
                        "killmail": eve_killmail,
                        "damage_done": attacker.damage_done,
                        "security_status": attacker.security_status,
                        "is_final_blow": attacker.is_final_blow,
                    },
                    **self._create_args_for_entities(attacker),
                }
                EveKillmailAttacker.objects.create(**args)

        eve_killmail.refresh_from_db()
        return eve_killmail

    @staticmethod
    def _create_args_for_entities(killmail_character: _KillmailCharacter) -> dict:
        args = dict()
        for prop_name in killmail_character.ENTITY_PROPS:
            entity_id = getattr(killmail_character, prop_name)
            if entity_id:
                field = prop_name.replace("_id", "")
                args[field], _ = EveEntity.objects.get_or_create(id=entity_id)

        return args

    def update_or_create_from_killmail(
        self, killmail: Killmail
    ) -> Tuple[models.Model, bool]:
        with transaction.atomic():
            try:
                self.get(id=killmail.id).delete()
                created = False
            except self.model.DoesNotExist:
                created = True

            obj = self.create_from_killmail(killmail)

        return obj, created
