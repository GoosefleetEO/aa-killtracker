from datetime import timedelta

from django.db import models
from django.utils.timezone import now

from allianceauth.services.hooks import get_extension_logger

from eveuniverse.models import EveEntity

from . import __title__
from .app_settings import KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


class KillmailQuerySet(models.QuerySet):
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


class KillmailManager(models.Manager):
    def get_queryset(self):
        return KillmailQuerySet(self.model, using=self._db)

    def delete_stale(self):
        """deletes all stale killmail"""
        deadline = now() - timedelta(days=KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS)
        self.filter(time__lt=deadline).delete()
