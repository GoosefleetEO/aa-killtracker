from celery import shared_task

from django.contrib.auth.models import User

from allianceauth.notifications import notify
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from eveuniverse.tasks import update_or_create_eve_object

from . import __title__
from .app_settings import KILLTRACKER_MAX_KILLMAILS_PER_RUN
from .helpers.killmails import Killmail
from .models import (
    Tracker,
    Webhook,
    EVE_CATEGORY_ID_SHIP,
    EVE_CATEGORY_ID_STRUCTURE,
)
from .utils import LoggerAddTag

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


@shared_task(base=QueueOnce)
def load_ship_types() -> None:
    """Loads all ship types"""
    logger.info("Started loading all ship types into eveuniverse")
    for category_id in [EVE_CATEGORY_ID_SHIP, EVE_CATEGORY_ID_STRUCTURE]:
        update_or_create_eve_object.delay(
            model_name="EveCategory",
            id=category_id,
            include_children=True,
            wait_for_children=False,
        )


@shared_task(base=QueueOnce)
def send_killmails_to_webhook(webhook_pk: int) -> None:
    try:
        webhook = Webhook.objects.get(pk=webhook_pk)
    except Webhook.DoesNotExist:
        logger.warning("Webhook with pk = %s does not exist", webhook_pk)
    else:
        if not webhook.is_enabled:
            logger.info("Tracker %s: Webhook disabled - skipping sending", webhook)
            return

        logger.info("Started sending killmails to webhook %s", webhook)
        webhook.send_queued_killmails()
        logger.info("Completed sending killmails to webhook %s", webhook)


@shared_task(base=QueueOnce, once={"keys": ["tracker_pk", "killmail_id"]})
def run_tracker(tracker_pk: int, killmail_id: id, killmail_json: str) -> None:
    try:
        tracker = Tracker.objects.get(pk=tracker_pk)
    except Tracker.DoesNotExist:
        logger.warning("Tracker with pk = %s does not exist", tracker_pk)
    else:
        logger.info("Started running tracker %s", tracker)
        killmail = Killmail.from_json(killmail_json)
        killmail_new = tracker.process_killmail(killmail)
        if killmail_new:
            tracker.webhook.add_killmail_to_queue(killmail_new)
            send_killmails_to_webhook.delay(webhook_pk=tracker.webhook.pk)

        logger.info("Finished running tracker %s", tracker)


@shared_task(base=QueueOnce)
def run_killtracker(max_killmails_in_total=KILLTRACKER_MAX_KILLMAILS_PER_RUN,) -> None:
    logger.info("Killtracker run started...")
    total_killmails = 0
    killmail = None
    while total_killmails < max_killmails_in_total:
        killmail = Killmail.fetch_from_zkb_redisq()
        if killmail:
            total_killmails += 1
        else:
            break

        for tracker in Tracker.objects.filter(is_enabled=True):
            run_tracker.delay(
                tracker_pk=tracker.pk,
                killmail_id=killmail.id,
                killmail_json=killmail.asjson(),
            )

    logger.info("Total killmails received from ZKB in this run: %d", total_killmails)


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
