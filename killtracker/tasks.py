from celery import shared_task

from django.contrib.auth.models import User

from allianceauth.notifications import notify
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from eveuniverse.models import EveEntity, EveCategory

from . import __title__
from .models import Killmail, Tracker, Webhook, EVE_CATEGORY_ID_SHIPS
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

MAX_KILLMAILS_IN_TOTAL = 50
MAX_KILLMAILS_PER_BATCH = 5


@shared_task(base=QueueOnce)
def load_ship_types() -> None:
    """Loads all ship types"""
    logger.info("Started loading all ship types into eveuniverse")
    EveCategory.objects.update_or_create_esi(
        id=EVE_CATEGORY_ID_SHIPS, include_children=True, wait_for_children=False
    )


@shared_task(base=QueueOnce)
def send_alerts_to_webhook(webhook_pk: int) -> None:
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.warning("Webhook with pk = %s does not exist", webhook_pk)
    else:
        logger.info("Started sending alerts to webhook %s", webhook)
        for tracker in Tracker.objects.filter(is_enabled=True, webhook=webhook):
            tracker.send_matching_to_webhook()

        logger.info("Completed sending alerts to webhook %s", webhook)


@shared_task(base=QueueOnce)
def run_tracker(tracker_pk: int) -> None:
    try:
        tracker = Tracker.objects.get(pk=tracker_pk)
    except Tracker.DoesNotExist:
        logger.warning("Tracker with pk = %s does not exist", tracker_pk)
    else:
        logger.info("Started running tracker %s", tracker)
        tracker.calculate_killmails()
        send_alerts_to_webhook.delay(webhook_pk=tracker.webhook.pk)
        logger.info("Finished running tracker %s", tracker)


@shared_task(base=QueueOnce)
def run_killtracker(
    max_killmails_in_total=MAX_KILLMAILS_IN_TOTAL,
    max_killmails_per_batch=MAX_KILLMAILS_PER_BATCH,
) -> None:
    logger.info("Killtracker started...")
    total_killmails = 0
    killmail = None
    while total_killmails < max_killmails_in_total:
        for _ in range(min(max_killmails_per_batch, max_killmails_in_total)):
            killmail = Killmail.objects.fetch_from_zkb()
            if killmail:
                total_killmails += 1
            else:
                break

        EveEntity.objects.bulk_update_new_esi()
        for tracker in Tracker.objects.filter(is_enabled=True):
            run_tracker.delay(tracker_pk=tracker.pk)

        if not killmail:
            break

    Killmail.objects.delete_stale()
    logger.info("Killtracker total killmails received: %d", total_killmails)


def send_test_message_to_webhook(webhook_pk: int, user_pk: int = None) -> None:
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.warning("Webhook with pk = %s does not exist", webhook_pk)
    else:
        logger.info("Sending test message to webhook %s", webhook)
        send_report, success = webhook.send_test_notification()

        if user_pk:
            try:
                user = User.objects.get(pk=user_pk)
            except User.DoesNotExist:
                logger.warning("User with pk = %s does not exist", user_pk)
            else:
                level = "success" if success else "error"
                notify(
                    user=user,
                    title=(
                        f"{__title__}: Result of test message to webhook {webhook}: "
                        f"{level.upper()}"
                    ),
                    message=send_report,
                    level=level,
                )
