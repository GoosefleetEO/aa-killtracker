import logging

from celery import shared_task

from allianceauth.services.tasks import QueueOnce

from .models import Killmail, Tracker, Webhook


logger = logging.getLogger(__name__)


@shared_task(base=QueueOnce)
def send_alerts_to_webhook(webhook_pk: int) -> None:
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.warning("Webhook with pk = %s does not exist", webhook_pk)
    else:
        for tracker in Tracker.objects.filter(is_activated=True, webhook=webhook):
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
def run_killtracker() -> None:
    logger.info("Killtracker started...")
    total_killmails = 0
    for _ in range(50):
        killmail = Killmail.objects.fetch_from_zkb()
        if killmail:
            total_killmails += 1
            for tracker in Tracker.objects.filter(is_activated=True):
                run_tracker.delay(tracker_pk=tracker.pk)
        else:
            break

    logger.info("Killtracker total killmails received: %d", total_killmails)
