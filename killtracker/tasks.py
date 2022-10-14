from celery import chain, shared_task

from django.db import IntegrityError
from eveuniverse.core.esitools import is_esi_online
from eveuniverse.tasks import update_unresolved_eve_entities

from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce
from app_utils.caching import cached_queryset
from app_utils.esi import retry_task_if_esi_is_down
from app_utils.logging import LoggerAddTag

from . import APP_NAME, __title__
from .app_settings import (
    KILLTRACKER_DISCORD_SEND_DELAY,
    KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES,
    KILLTRACKER_GENERATE_MESSAGE_RETRY_COUNTDOWN,
    KILLTRACKER_MAX_KILLMAILS_PER_RUN,
    KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS,
    KILLTRACKER_STORING_KILLMAILS_ENABLED,
    KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
    KILLTRACKER_TASKS_TIMEOUT,
)
from .core.killmails import Killmail
from .exceptions import WebhookTooManyRequests
from .models import EveKillmail, Tracker, Webhook

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def run_killtracker(runs: int = 0) -> None:
    """Main task for running the Killtracker.

    Will fetch new killmails from ZKB and start running trackers for them
    """
    if not is_esi_online():
        logger.warning("ESI is currently offline. Aborting")
        return

    if runs == 0:
        logger.info("Killtracker run started...")
        qs = cached_queryset(
            Webhook.objects.filter(is_enabled=True),
            key=f"{APP_NAME}_enabled_webhooks",
            timeout=KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
        )
        for webhook in qs:
            webhook.reset_failed_messages()

    killmail = Killmail.create_from_zkb_redisq()
    if killmail:
        killmail.save()
        qs = cached_queryset(
            Tracker.objects.filter(is_enabled=True),
            key=f"{APP_NAME}_enabled_trackers",
            timeout=KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
        )
        for tracker in qs:
            run_tracker.delay(tracker_pk=tracker.pk, killmail_id=killmail.id)

        if KILLTRACKER_STORING_KILLMAILS_ENABLED:
            chain(
                store_killmail.si(killmail.id),
                update_unresolved_eve_entities.si(),
            ).delay()

    total_killmails = runs + (1 if killmail else 0)
    if killmail and total_killmails < KILLTRACKER_MAX_KILLMAILS_PER_RUN:
        run_killtracker.delay(runs=runs + 1)
    else:
        if (
            KILLTRACKER_STORING_KILLMAILS_ENABLED
            and KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS > 0
        ):
            delete_stale_killmails.delay()

        logger.info(
            "Killtracker runs completed. %d killmails received from ZKB",
            total_killmails,
        )


@shared_task(bind=True, max_retries=None)
def run_tracker(
    self, tracker_pk: int, killmail_id: int, ignore_max_age: bool = False
) -> None:
    """Run tracker for given killmail and trigger sending if needed."""
    retry_task_if_esi_is_down(self)
    tracker = Tracker.objects.get_cached(
        pk=tracker_pk,
        select_related="webhook",
        timeout=KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
    )
    logger.info("%s: Started running tracker", tracker)
    killmail = Killmail.get(killmail_id)
    killmail_new = tracker.process_killmail(
        killmail=killmail, ignore_max_age=ignore_max_age
    )
    if killmail_new:
        generate_killmail_message.delay(tracker_pk=tracker_pk, killmail_id=killmail_id)
    elif tracker.webhook.main_queue.size():
        send_messages_to_webhook.delay(webhook_pk=tracker.webhook.pk)


@shared_task(bind=True, max_retries=None)
def generate_killmail_message(self, tracker_pk: int, killmail_id: int) -> None:
    """Generate and enqueue message from given killmail and start sending."""
    retry_task_if_esi_is_down(self)
    tracker = Tracker.objects.get_cached(
        pk=tracker_pk,
        select_related="webhook",
        timeout=KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
    )
    killmail = Killmail.get(killmail_id)
    logger.info("%s: Generating message from killmail %s", tracker, killmail.id)
    try:
        tracker.generate_killmail_message(killmail)
    except Exception as ex:
        will_retry = self.request.retries < KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES
        logger.warning(
            "%s: Failed to generate killmail %s.%s",
            tracker,
            killmail.id,
            " Will retry." if will_retry else "",
            exc_info=True,
        )
        raise self.retry(
            max_retries=KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES,
            countdown=KILLTRACKER_GENERATE_MESSAGE_RETRY_COUNTDOWN,
            exc=ex,
        )
    else:
        send_messages_to_webhook.delay(webhook_pk=tracker.webhook.pk)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def store_killmail(killmail_id: int) -> None:
    """stores killmail as EveKillmail object"""
    killmail = Killmail.get(killmail_id)
    try:
        EveKillmail.objects.create_from_killmail(killmail, resolve_ids=False)
    except IntegrityError:
        logger.warning(
            "%s: Failed to store killmail, because it already exists", killmail.id
        )
    else:
        logger.info("%s: Stored killmail", killmail.id)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def delete_stale_killmails() -> None:
    """deleted all EveKillmail objects that are considered stale"""
    _, details = EveKillmail.objects.delete_stale()
    if details:
        logger.info("Deleted %d stale killmails", details["killtracker.EveKillmail"])


@shared_task(
    bind=True,
    base=QueueOnce,  # celery_once locks stay intact during retries
    timeout=KILLTRACKER_TASKS_TIMEOUT,
    retry_backoff=False,
    max_retries=None,
)
def send_messages_to_webhook(self, webhook_pk: int) -> None:
    """send all queued messages to given Webhook"""

    webhook = Webhook.objects.get_cached(
        pk=webhook_pk,
        timeout=KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT,
    )
    if not webhook.is_enabled:
        logger.info("%s: Webhook is disabled - aborting", webhook)
        return

    message = webhook.main_queue.dequeue()
    if message:
        logger.info("%s: Sending message to webhook", webhook)
        try:
            response = webhook.send_message_to_webhook(message)
        except WebhookTooManyRequests as ex:
            webhook.main_queue.enqueue(message)
            logger.warning(
                "%s: Too many requests for webhook. Blocked for %s seconds. Aborting.",
                webhook,
                ex.retry_after,
            )
            return

        if not response.status_ok:
            webhook.error_queue.enqueue(message)
            logger.warning(
                "%s: Failed to send message to webhook, will retry. "
                "HTTP status code: %d, response: %s",
                webhook,
                response.status_code,
                response.content,
            )

        raise self.retry(countdown=KILLTRACKER_DISCORD_SEND_DELAY)
    else:
        logger.debug("%s: No more messages to send for webhook", webhook)


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def send_test_message_to_webhook(webhook_pk: int, count: int = 1) -> None:
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
