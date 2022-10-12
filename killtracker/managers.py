from datetime import timedelta
from typing import Dict, Tuple

from django.db import models, transaction
from django.utils.timezone import now
from eveuniverse.models import EveEntity

from allianceauth.services.hooks import get_extension_logger
from app_utils.caching import ObjectCacheMixin
from app_utils.logging import LoggerAddTag

from . import __title__
from .app_settings import KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS
from .core.killmails import Killmail, _KillmailCharacter

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


class EveTypePlusManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related("eve_group")


class EveKillmailQuerySet(models.QuerySet):
    """Custom queryset for EveKillmail"""

    def load_entities(self) -> int:
        """Load all unknown entities for killmails in this QuerySet.

        Return count of updated entities.
        """
        entity_ids = set()
        for killmail in self:
            entity_ids |= killmail.entity_ids()
        return EveEntity.objects.filter(id__in=entity_ids, name="").update_from_esi()


class EveKillmailBaseManager(models.Manager):
    def delete_stale(self) -> Tuple[int, Dict[str, int]]:
        """deletes all stale killmail"""
        if KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS > 0:
            deadline = now() - timedelta(days=KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS)
            return self.filter(time__lt=deadline).delete()

    def create_from_killmail(
        self, killmail: Killmail, resolve_ids=True
    ) -> models.Model:
        """create a new EveKillmail from a Killmail object and returns it

        Args:
        - resolve_ids: When set to False will not resolve EveEntity IDs

        """
        from .models import EveKillmailAttacker

        params = {
            "id": killmail.id,
            "time": killmail.time,
            "damage_taken": killmail.victim.damage_taken,
            "position_x": killmail.position.x,
            "position_y": killmail.position.y,
            "position_z": killmail.position.z,
        }
        victim = self._create_args_for_entities(killmail.victim)
        params.update(victim)
        if killmail.solar_system_id:
            params["solar_system"], _ = EveEntity.objects.get_or_create(
                id=killmail.solar_system_id
            )
        if killmail.zkb:
            zkb = killmail.zkb.asdict()
            zkb["zkb_points"] = zkb.pop("points")
            params.update(zkb)
        eve_killmail = self.create(**params)
        if killmail.attackers:
            attacker_objs = []
            for attacker in killmail.attackers:
                params = {
                    **{
                        "killmail": eve_killmail,
                        "damage_done": attacker.damage_done,
                        "security_status": attacker.security_status,
                        "is_final_blow": attacker.is_final_blow,
                    },
                    **self._create_args_for_entities(attacker),
                }
                attacker_objs.append(EveKillmailAttacker(**params))
            EveKillmailAttacker.objects.bulk_create(attacker_objs)
        if resolve_ids:
            eve_killmail.load_entities()
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
            obj = self.create_from_killmail(killmail, resolve_ids=False)
        obj.load_entities()
        return obj, created


EveKillmailManager = EveKillmailBaseManager.from_queryset(EveKillmailQuerySet)


class TrackerManager(ObjectCacheMixin, models.Manager):
    pass


class WebhookManager(ObjectCacheMixin, models.Manager):
    pass
