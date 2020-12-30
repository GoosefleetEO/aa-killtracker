from time import sleep

from celery import shared_task, chain

from django.db import IntegrityError
from django.contrib.auth.models import User
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from eveuniverse.tasks import update_unresolved_eve_entities

from allianceauth.notifications import notify
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from . import __title__
from .app_settings import (
    KILLTRACKER_MAX_KILLMAILS_PER_RUN,
    KILLTRACKER_MAX_DURATION_PER_RUN,
    KILLTRACKER_STORING_KILLMAILS_ENABLED,
    KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS,
    KILLTRACKER_TASKS_TIMEOUT,
    KILLTRACKER_DISCORD_SEND_DELAY,
)
from .core.killmails import Killmail
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
            tracker.webhook.add_killmail_to_queue(killmail_new)

        if killmail_new or tracker.webhook.queue_size():
            send_killmails_to_webhook.delay(webhook_pk=tracker.webhook.pk)

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


@shared_task(base=QueueOnce, timeout=KILLTRACKER_TASKS_TIMEOUT)
def send_killmails_to_webhook(webhook_pk: int) -> None:
    """send all currently queued killmails in given webhook object to Discord"""
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.error("Webhook with pk = %s does not exist", webhook_pk)
        return

    if not webhook.is_enabled:
        logger.info("Webhook %s disabled - skipping sending", webhook)
        return

    logger.info("Started sending killmails to webhook %s", webhook)

    failed_killmails = list()
    killmail_counter = 0
    while True:
        message = webhook._queue.dequeue()
        if message:
            killmail = Killmail.from_json(message)
            logger.debug(
                "Sending killmail with ID %d to webhook %s", killmail.id, webhook
            )
            sleep(KILLTRACKER_DISCORD_SEND_DELAY)
            if webhook.send_killmail(killmail):
                killmail_counter += 1
            else:
                failed_killmails.append(killmail)
        else:
            break

    if failed_killmails:
        for killmail in failed_killmails:
            webhook.add_killmail_to_queue(killmail)

    logger.info("Completed sending killmails to webhook %s", webhook)
    return killmail_counter


@shared_task(timeout=KILLTRACKER_TASKS_TIMEOUT)
def send_test_message_to_webhook(webhook_pk: int, user_pk: int = None) -> None:
    """send a test message to given webhook.
    Optional inform user about result if user ok is given
    """
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.error("Webhook with pk = %s does not exist", webhook_pk)
    else:
        logger.info("Sending test message to webhook %s", webhook)
        error_text, success = webhook.send_test_message()

        if user_pk:
            message = (
                f"Error text: {error_text}\nCheck log files for details."
                if not success
                else "No errors"
            )
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
                    message=message,
                    level=level,
                )
