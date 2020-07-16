from celery import shared_task

from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from eveuniverse.models import EveEntity

from . import __title__
from .models import Killmail, Tracker, Webhook
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

MAX_KILLMAILS_IN_TOTAL = 50
MAX_KILLMAILS_PER_BATCH = 5


@shared_task(base=QueueOnce)
def send_alerts_to_webhook(webhook_pk: int) -> None:
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.warning("Webhook with pk = %s does not exist", webhook_pk)
    else:
        for tracker in Tracker.objects.filter(is_enabled=True, webhook=webhook):
            tracker.send_matching_to_webhook()


@shared_task(base=QueueOnce)
def run_tracker(tracker_pk: int) -> None:
    try:
        tracker = Tracker.objects.get(pk=tracker_pk)
    except Tracker.DoesNotExist:
        logger.warning("Tracker with pk = %s does not exist", tracker_pk)
    else:
        tracker.calculate_killmails()
        send_alerts_to_webhook.delay(webhook_pk=tracker.webhook.pk)


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
