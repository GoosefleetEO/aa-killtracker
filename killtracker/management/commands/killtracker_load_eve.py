import logging
from django.core.management import call_command
from django.core.management.base import BaseCommand

from app_utils.logging import LoggerAddTag

from ... import __title__
from ...constants import (
    EVE_CATEGORY_ID_SHIP,
    EVE_CATEGORY_ID_STRUCTURE,
    EVE_CATEGORY_ID_FIGHTER,
    EVE_GROUP_MINING_DRONE,
    EVE_GROUP_ORBITAL_INFRASTRUCTURE,
)


logger = LoggerAddTag(logging.getLogger(__name__), __title__)


class Command(BaseCommand):
    help = "Preloads data required for this app from ESI"

    def handle(self, *args, **options):
        call_command(
            "eveuniverse_load_types",
            __title__,
            "--category_id",
            str(EVE_CATEGORY_ID_SHIP),
            "--category_id",
            str(EVE_CATEGORY_ID_STRUCTURE),
            "--category_id",
            str(EVE_CATEGORY_ID_FIGHTER),
            "--group_id",
            str(EVE_GROUP_ORBITAL_INFRASTRUCTURE),
            "--group_id",
            str(EVE_GROUP_MINING_DRONE),
        )
