import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand

from app_utils.logging import LoggerAddTag

from ... import __title__
from ...constants import EveCategoryId, EveGroupId

logger = LoggerAddTag(logging.getLogger(__name__), __title__)


class Command(BaseCommand):
    help = "Preloads data required for this app from ESI"

    def handle(self, *args, **options):
        call_command(
            "eveuniverse_load_types",
            __title__,
            "--category_id",
            str(EveCategoryId.ENTITY.value),
            "--category_id",
            str(EveCategoryId.SHIP.value),
            "--category_id",
            str(EveCategoryId.STRUCTURE.value),
            "--category_id",
            str(EveCategoryId.FIGHTER.value),
            "--group_id",
            str(EveGroupId.ORBITAL_INFRASTRUCTURE.value),
            "--group_id",
            str(EveGroupId.MINING_DRONE.value),
        )
