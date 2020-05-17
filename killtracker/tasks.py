import logging

from celery import shared_task

from .models import Killmail


logger = logging.getLogger(__name__)


@shared_task
def fetch_killmails():
    Killmail.objects.fetch_killmails()


@shared_task
def process_killmails():    
    Killmail.objects.process_killmails()
