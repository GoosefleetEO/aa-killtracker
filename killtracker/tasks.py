from timeit import default_timer as timer

from celery import shared_task

from django.db import IntegrityError
from django.contrib.auth.models import User

from allianceauth.notifications import notify
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from . import __title__
from .app_settings import (
    KILLTRACKER_MAX_KILLMAILS_PER_RUN,
    KILLTRACKER_STORING_KILLMAILS_ENABLED,
    KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS,
)
from .core.killmails import Killmail
from .models import (
    EveKillmail,
    Tracker,
    Webhook,
)
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


@shared_task
def store_killmail(killmail_json: str) -> None:
    """stores killmail as EveKillmail object"""
    killmail = Killmail.from_json(killmail_json)
    try:
        EveKillmail.objects.create_from_killmail(killmail)
    except IntegrityError:
        logger.warning(
            "Failed to store killmail with ID %d, because it already exists",
            killmail.id,
        )
    else:
        logger.info("Stored killmail with ID %d", killmail.id)


@shared_task
def delete_stale_killmails() -> None:
    """deleted all EveKillmail objects that are considered stale"""
    _, details = EveKillmail.objects.delete_stale()
    if details:
        logger.info("Deleted %d stale killmails", details["killtracker.EveKillmail"])


@shared_task(base=QueueOnce)
def send_killmails_to_webhook(webhook_pk: int) -> None:
    """send all currently queued killmails in given webhook object to Discord"""
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.error("Webhook with pk = %s does not exist", webhook_pk)
    else:
        if not webhook.is_enabled:
            logger.info("Webhook %s disabled - skipping sending", webhook)
            return

        logger.info("Started sending killmails to webhook %s", webhook)
        webhook.send_queued_killmails()
        logger.info("Completed sending killmails to webhook %s", webhook)


@shared_task
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
            send_killmails_to_webhook.delay(webhook_pk=tracker.webhook.pk)

        logger.info("Finished running tracker %s", tracker)


@shared_task(base=QueueOnce)
def run_killtracker(max_killmails_in_total=KILLTRACKER_MAX_KILLMAILS_PER_RUN,) -> None:
    """Main task for running the Killtracker. 
    Will fetch new killmails from ZKB and start running trackers for them

    Params:
    - max_killmails_in_total: override the default number of max killmails 
    received per run 
    """
    start = timer()
    logger.info("Killtracker run started...")
    total_killmails = 0
    killmail = None
    while total_killmails < max_killmails_in_total:
        killmail = Killmail.create_from_zkb_redisq()
        if killmail:
            total_killmails += 1
        else:
            break

        killmail_json = killmail.asjson()
        for tracker in Tracker.objects.filter(is_enabled=True):
            run_tracker.delay(
                tracker_pk=tracker.pk, killmail_json=killmail_json,
            )

        if KILLTRACKER_STORING_KILLMAILS_ENABLED:
            store_killmail.delay(killmail_json=killmail_json)

    if (
        KILLTRACKER_STORING_KILLMAILS_ENABLED
        and KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS > 0
    ):
        delete_stale_killmails.delay()

    end = timer()
    logger.info(
        "Total killmails received from ZKB in %d secs: %d", end - start, total_killmails
    )


@shared_task
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
