import logging

from celery import shared_task

from allianceauth.services.tasks import QueueOnce

from .models import Killmail, Tracker, Webhook


logger = logging.getLogger(__name__)


@shared_task(bind=True, base=QueueOnce)
def send_alerts_to_webhook(self, webhook_pk: int) -> None:
    webhook = Webhook.objects.get(pk=webhook_pk)
    for tracker in Tracker.objects.filter(webhook=webhook):
        tracker.send_matching_to_webhook()


@shared_task(bind=True, base=QueueOnce)
def run_tracker(self, tracker_pk: int) -> None:
    tracker = Tracker.objects.get(pk=tracker_pk)
    tracker.calculate_killmails()
    send_alerts_to_webhook.delay(webhook_pk=tracker.webhook.pk)


@shared_task
def run_all_tracker_and_send() -> None:
    for tracker in Tracker.objects.filter(is_activated=True):
        run_tracker.delay(tracker_pk=tracker.pk)


@shared_task(bind=True, base=QueueOnce)
def run_killtracker(self):
    total_killmails = 0
    while total_killmails < 100:
        killmails_fetched = Killmail.objects.fetch_from_zkb(max_killmails=10)
        total_killmails += killmails_fetched        
        run_all_tracker_and_send.delay()
        if killmails_fetched == 0:        
            break
