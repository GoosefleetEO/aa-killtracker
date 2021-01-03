from celery import shared_task, chain

from django.db import IntegrityError
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from eveuniverse.tasks import update_unresolved_eve_entities

from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from . import __title__
from .app_settings import (
    KILLTRACKER_MAX_KILLMAILS_PER_RUN,
    KILLTRACKER_MAX_DURATION_PER_RUN,
    KILLTRACKER_STORING_KILLMAILS_ENABLED,
    KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS,
    KILLTRACKER_TASKS_TIMEOUT,
)
from .core.killmails import Killmail
from .exceptions import WebhookRateLimitReached, WebhookTooManyRequests
from .models import (
    EveKillmail,
    Tracker,
    Webhook,
)
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def run_killtracker(
    killmails_max: int = KILLTRACKER_MAX_KILLMAILS_PER_RUN,
    killmails_count: int = 0,
    started_str: str = None,
) -> None:
    """Main task for running the Killtracker.
    Will fetch new killmails from ZKB and start running trackers for them

    Params:
    - killmails_max: override the default number of max killmails
    received per run
    - killmails_count: internal parameter
    - started_str: internal parameter
    """
    if killmails_count == 0:
        logger.info("Killtracker run started...")
        for webhook in Webhook.objects.filter(is_enabled=True):
            webhook.reset_failed_messages()

    started = now() if not started_str else parse_datetime(started_str)
    duration = (now() - started).total_seconds()
    if duration > KILLTRACKER_MAX_DURATION_PER_RUN:
        # need to ensure this run finishes before CRON starts the next
        logger.info("Soft timeout reached. Aborting run.")
        killmail = None
    else:
        killmail = Killmail.create_from_zkb_redisq()

    if killmail:
        killmails_count += 1
        killmail_json = killmail.asjson()
        for tracker in Tracker.objects.filter(is_enabled=True):
            run_tracker.delay(
                tracker_pk=tracker.pk,
                killmail_json=killmail_json,
            )

        if KILLTRACKER_STORING_KILLMAILS_ENABLED:
            chain(
                store_killmail.si(killmail_json=killmail_json),
                update_unresolved_eve_entities.si(),
            ).delay()

    if killmail and killmails_count < killmails_max:
        run_killtracker.delay(
            killmails_max=killmails_max,
            killmails_count=killmails_count,
            started_str=started.isoformat(),
        )
    else:
        if (
            KILLTRACKER_STORING_KILLMAILS_ENABLED
            and KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS > 0
        ):
            delete_stale_killmails.delay()

        logger.info(
            "Killtracker completed. %d killmails received from ZKB in %d seconds",
            killmails_count,
            duration,
        )


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def run_tracker(
    tracker_pk: int, killmail_json: str, ignore_max_age: bool = False
) -> None:
    """run tracker for given killmail and trigger sending killmails if it matches"""
    try:
        tracker = Tracker.objects.get(pk=tracker_pk)
    except Tracker.DoesNotExist:
        logger.error("Tracker with pk = %s does not exist", tracker_pk)
    else:
        logger.info("Started running tracker %s", tracker)
        killmail = Killmail.from_json(killmail_json)
        killmail_new = tracker.process_killmail(
            killmail=killmail, ignore_max_age=ignore_max_age
        )
        if killmail_new:
            tracker.enqueue_killmail(killmail_new)

        if killmail_new or tracker.webhook.main_queue.size():
            send_messages_to_webhook.delay(webhook_pk=tracker.webhook.pk)

        logger.info("Finished running tracker %s", tracker)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def store_killmail(killmail_json: str) -> None:
    """stores killmail as EveKillmail object"""
    killmail = Killmail.from_json(killmail_json)
    try:
        EveKillmail.objects.create_from_killmail(killmail, resolve_ids=False)
    except IntegrityError:
        logger.warning(
            "Failed to store killmail with ID %d, because it already exists",
            killmail.id,
        )
    else:
        logger.info("Stored killmail with ID %d", killmail.id)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def delete_stale_killmails() -> None:
    """deleted all EveKillmail objects that are considered stale"""
    _, details = EveKillmail.objects.delete_stale()
    if details:
        logger.info("Deleted %d stale killmails", details["killtracker.EveKillmail"])


@shared_task(
    bind=True,
    base=QueueOnce,  # celery_once locks stay intact during retries
    once={"timeout": 60 * 60 * 6},  # too many requests delays can be huge
    timeout=KILLTRACKER_TASKS_TIMEOUT,
    retry_backoff=False,
    max_retries=None,
)
def send_messages_to_webhook(self, webhook_pk: int) -> None:
    """send all queued messages to given Webhook"""
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.error("Webhook with pk = %s does not exist", webhook_pk)
        return

    if not webhook.is_enabled:
        logger.info("Webhook %s disabled - aborting", webhook)
        return

    message = webhook.main_queue.dequeue()
    if message:
        logger.debug("Sending message to webhook %s", self)
        try:
            success = webhook.send_message_to_webhook(message)
        except WebhookTooManyRequests as ex:
            webhook.main_queue.enqueue(message)
            self.retry(countdown=ex.reset_after)
        except WebhookRateLimitReached as ex:
            self.retry(countdown=ex.reset_after)
        else:
            if not success:
                webhook.error_queue.enqueue(message)
            if webhook.main_queue.size():
                self.retry(countdown=0)
    else:
        logger.info("No queued killmails for webhook %s", webhook)


@shared_task(bind=True, timeout=KILLTRACKER_TASKS_TIMEOUT)
def send_test_message_to_webhook(self, webhook_pk: int, count: int = 1) -> None:
    """send a test message to given webhook.
    Optional inform user about result if user ok is given
    """
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.error("Webhook with pk = %s does not exist", webhook_pk)
        return

    logger.info("Sending %s test messages to webhook %s", count, webhook)
    for n in range(count):
        num_str = f"{n+1}/{count} " if count > 1 else ""
        webhook.enqueue_message(content=f"Test message {num_str}from {__title__}.")

    send_messages_to_webhook.delay(webhook.pk)
