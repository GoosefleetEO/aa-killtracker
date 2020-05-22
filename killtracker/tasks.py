import logging

from celery import shared_task

from .models import Killmail, Tracker


logger = logging.getLogger(__name__)


@shared_task
def fetch_killmails():
    Killmail.objects.fetch_from_zkb()


@shared_task
def run_tracker(tracker_pk: int) -> None:
    tracker = Tracker.objects.get(pk=tracker_pk)
    tracker.calculate_killmails()
    tracker.send_matching_to_webhook()
