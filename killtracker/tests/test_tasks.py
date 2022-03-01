from unittest.mock import patch

import celery
import dhooks_lite

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings

from ..exceptions import WebhookTooManyRequests
from ..models import EveKillmail, Tracker, Webhook
from ..tasks import (
    delete_stale_killmails,
    generate_killmail_message,
    run_killtracker,
    run_tracker,
    send_messages_to_webhook,
    send_test_message_to_webhook,
    store_killmail,
)
from .testdata.helpers import LoadTestDataMixin, load_eve_killmails, load_killmail

MODULE_PATH = "killtracker.tasks"


class CeleryRequestStub(object):
    def __init__(self):
        self.retries = 0


class TestTrackerBase(LoadTestDataMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )
        cls.tracker_2 = Tracker.objects.create(
            name="High Sec Only",
            exclude_low_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )


@override_settings(CELERY_ALWAYS_EAGER=True)
@patch(MODULE_PATH + ".is_esi_online")
@patch(MODULE_PATH + ".delete_stale_killmails")
@patch(MODULE_PATH + ".store_killmail")
@patch(MODULE_PATH + ".Killmail.create_from_zkb_redisq")
@patch(MODULE_PATH + ".run_tracker")
class TestRunKilltracker(TestTrackerBase):
    def setUp(self) -> None:
        cache.clear()

    @staticmethod
    def my_fetch_from_zkb():
        for killmail_id in [10000001, 10000002, 10000003, None]:
            if killmail_id:
                yield load_killmail(killmail_id)
            else:
                yield None

    @patch(MODULE_PATH + ".KILLTRACKER_STORING_KILLMAILS_ENABLED", False)
    def test_should_run_normally(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
        mock_is_esi_online,
    ):
        # given
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()
        mock_is_esi_online.return_value = True
        self.webhook_1.error_queue.enqueue(load_killmail(10000004).asjson())
        # when
        run_killtracker.delay()
        # then
        self.assertEqual(mock_run_tracker.delay.call_count, 6)
        self.assertEqual(mock_store_killmail.si.call_count, 0)
        self.assertFalse(mock_delete_stale_killmails.delay.called)
        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        self.assertEqual(self.webhook_1.error_queue.size(), 0)

    @patch(MODULE_PATH + ".KILLTRACKER_STORING_KILLMAILS_ENABLED", False)
    def test_should_stop_when_esi_is_offline(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
        mock_is_esi_online,
    ):
        # given
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()
        mock_is_esi_online.return_value = False
        # when
        run_killtracker.delay()
        # then
        self.assertEqual(mock_run_tracker.delay.call_count, 0)
        self.assertEqual(mock_store_killmail.si.call_count, 0)
        self.assertFalse(mock_delete_stale_killmails.delay.called)

    @patch(MODULE_PATH + ".KILLTRACKER_MAX_KILLMAILS_PER_RUN", 2)
    def test_should_stop_when_max_killmails_received(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
        mock_is_esi_online,
    ):
        # given
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()
        mock_is_esi_online.return_value = True
        # when
        run_killtracker.delay()
        # then
        self.assertEqual(mock_run_tracker.delay.call_count, 4)

    @patch(MODULE_PATH + ".KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS", 30)
    @patch(MODULE_PATH + ".KILLTRACKER_STORING_KILLMAILS_ENABLED", True)
    def test_can_store_killmails(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
        mock_is_esi_online,
    ):
        # given
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()
        mock_is_esi_online.return_value = True
        # when
        run_killtracker.delay()
        # then
        self.assertEqual(mock_run_tracker.delay.call_count, 6)
        self.assertEqual(mock_store_killmail.si.call_count, 3)
        self.assertTrue(mock_delete_stale_killmails.delay.called)


@patch(MODULE_PATH + ".send_messages_to_webhook")
@patch(MODULE_PATH + ".generate_killmail_message")
class TestRunTracker(TestTrackerBase):
    def setUp(self) -> None:
        cache.clear()

    def test_call_enqueue_for_matching_killmail(
        self, mock_enqueue_killmail_message, mock_send_messages_to_webhook
    ):
        """when killmail is matching, then generate new message from it"""
        killmail_json = load_killmail(10000001).asjson()
        run_tracker(self.tracker_1.pk, killmail_json)
        self.assertTrue(mock_enqueue_killmail_message.delay.called)
        self.assertFalse(mock_send_messages_to_webhook.delay.called)

    def test_do_nothing_when_no_matching_killmail(
        self, mock_enqueue_killmail_message, mock_send_messages_to_webhook
    ):
        """when killmail is not matching and webhook queue is empty,
        then do nothing
        """
        killmail_json = load_killmail(10000003).asjson()
        run_tracker(self.tracker_1.pk, killmail_json)
        self.assertFalse(mock_enqueue_killmail_message.delay.called)
        self.assertFalse(mock_send_messages_to_webhook.delay.called)

    def test_start_message_sending_when_queue_non_empty(
        self, mock_enqueue_killmail_message, mock_send_messages_to_webhook
    ):
        """when killmail is not matching and webhook queue is not empty,
        then start sending anyway
        """
        killmail_json = load_killmail(10000003).asjson()
        self.webhook_1.enqueue_message(content="test")
        run_tracker(self.tracker_1.pk, killmail_json)
        self.assertFalse(mock_enqueue_killmail_message.delay.called)
        self.assertTrue(mock_send_messages_to_webhook.delay.called)


@patch(MODULE_PATH + ".generate_killmail_message.retry")
@patch(MODULE_PATH + ".send_messages_to_webhook")
class TestGenerateKillmailMessage(TestTrackerBase):
    def setUp(self) -> None:
        cache.clear()
        self.retries = 0
        self.killmail_json = load_killmail(10000001).asjson()

    def my_retry(self, *args, **kwargs):
        self.retries += 1
        if self.retries > kwargs["max_retries"]:
            raise kwargs["exc"]
        generate_killmail_message(self.tracker_1.pk, self.killmail_json)

    def test_normal(self, mock_send_messages_to_webhook, mock_retry):
        """enqueue generated killmail and start sending"""
        mock_retry.side_effect = self.my_retry

        generate_killmail_message(self.tracker_1.pk, self.killmail_json)

        self.assertTrue(mock_send_messages_to_webhook.delay.called)
        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        self.assertFalse(mock_retry.called)

    @patch(MODULE_PATH + ".KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES", 3)
    @patch(MODULE_PATH + ".Tracker.generate_killmail_message")
    def test_retry_until_maximum(
        self, mock_generate_killmail_message, mock_send_messages_to_webhook, mock_retry
    ):
        """when message generation fails,then retry until max retries is reached"""
        mock_retry.side_effect = self.my_retry
        mock_generate_killmail_message.side_effect = RuntimeError

        with self.assertRaises(RuntimeError):
            generate_killmail_message(self.tracker_1.pk, self.killmail_json)

        self.assertFalse(mock_send_messages_to_webhook.delay.called)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.assertEqual(mock_retry.call_count, 4)


@patch(MODULE_PATH + ".send_messages_to_webhook.retry")
@patch(MODULE_PATH + ".Webhook.send_message_to_webhook")
@patch(MODULE_PATH + ".logger")
class TestSendMessagesToWebhook(TestTrackerBase):
    def setUp(self) -> None:
        cache.clear()

    def my_retry(self, *args, **kwargs):
        send_messages_to_webhook(self.webhook_1.pk)

    def test_one_message(self, mock_logger, mock_send_message_to_webhook, mock_retry):
        """when one mesage in queue, then send it and retry with delay"""
        mock_retry.side_effect = self.my_retry
        mock_send_message_to_webhook.return_value = dhooks_lite.WebhookResponse(
            {}, status_code=200
        )
        self.webhook_1.enqueue_message(content="Test message")

        send_messages_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_message_to_webhook.call_count, 1)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.assertEqual(self.webhook_1.error_queue.size(), 0)
        self.assertEqual(mock_retry.call_count, 1)
        _, kwargs = mock_retry.call_args
        self.assertEqual(kwargs["countdown"], 2)
        self.assertFalse(mock_logger.error.called)
        self.assertFalse(mock_logger.warning.called)

    def test_three_message(self, mock_logger, mock_send_message_to_webhook, mock_retry):
        """when three mesages in queue, then sends them and returns 3"""
        mock_retry.side_effect = self.my_retry
        mock_send_message_to_webhook.return_value = dhooks_lite.WebhookResponse(
            {}, status_code=200
        )
        self.webhook_1.enqueue_message(content="Test message")
        self.webhook_1.enqueue_message(content="Test message")
        self.webhook_1.enqueue_message(content="Test message")

        send_messages_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_message_to_webhook.call_count, 3)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.assertEqual(self.webhook_1.error_queue.size(), 0)
        self.assertTrue(mock_retry.call_count, 4)

    def test_no_messages(self, mock_logger, mock_send_message_to_webhook, mock_retry):
        """when no mesages in queue, then do nothing"""
        mock_retry.side_effect = self.my_retry
        mock_send_message_to_webhook.return_value = dhooks_lite.WebhookResponse(
            {}, status_code=200
        )

        send_messages_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_message_to_webhook.call_count, 0)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.assertEqual(self.webhook_1.error_queue.size(), 0)
        self.assertEqual(mock_retry.call_count, 0)
        self.assertFalse(mock_logger.error.called)
        self.assertFalse(mock_logger.warning.called)

    def test_failed_message(
        self, mock_logger, mock_send_message_to_webhook, mock_retry
    ):
        """when message sending failed,
        then put message in error queue and log warning
        """
        mock_retry.side_effect = self.my_retry
        mock_send_message_to_webhook.return_value = dhooks_lite.WebhookResponse(
            {}, status_code=404
        )
        self.webhook_1.enqueue_message(content="Test message")

        send_messages_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_message_to_webhook.call_count, 1)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.assertEqual(self.webhook_1.error_queue.size(), 1)
        self.assertTrue(mock_logger.warning.called)

    def test_abort_on_too_many_requests(
        self, mock_logger, mock_send_message_to_webhook, mock_retry
    ):
        """
        when WebhookTooManyRequests exception is raised
        then message is re-queued and retry once
        """
        mock_retry.side_effect = self.my_retry
        mock_send_message_to_webhook.side_effect = WebhookTooManyRequests(10)
        self.webhook_1.enqueue_message(content="Test message")

        send_messages_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_message_to_webhook.call_count, 1)
        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        self.assertFalse(mock_retry.called)

    def test_log_info_if_not_enabled(
        self, mock_logger, mock_send_message_to_webhook, mock_retry
    ):
        my_webhook = Webhook.objects.create(
            name="disabled", url="dummy-url-2", is_enabled=False
        )
        send_messages_to_webhook(my_webhook.pk)

        self.assertFalse(mock_send_message_to_webhook.called)
        self.assertTrue(mock_logger.info.called)


@patch(MODULE_PATH + ".logger")
class TestStoreKillmail(TestTrackerBase):
    def test_normal(self, mock_logger):
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()
        store_killmail(killmail_json)

        self.assertTrue(EveKillmail.objects.filter(id=10000001).exists())
        self.assertFalse(mock_logger.warning.called)

    def test_already_exists(self, mock_logger):
        load_eve_killmails([10000001])
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()
        store_killmail(killmail_json)

        self.assertTrue(mock_logger.warning.called)


@override_settings(CELERY_ALWAYS_EAGER=True, CELERY_EAGER_PROPAGATES_EXCEPTIONS=True)
@patch("killtracker.models.dhooks_lite.Webhook.execute")
@patch(MODULE_PATH + ".logger")
class TestSendTestKillmailsToWebhook(TestTrackerBase):
    def setUp(self) -> None:
        self.webhook_1.main_queue.clear()

    def test_run_normal(self, mock_logger, mock_execute):
        # given
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        # when
        with self.assertRaises(celery.exceptions.Retry):
            send_test_message_to_webhook.delay(self.webhook_1.pk)
        # then
        self.assertTrue(mock_execute.called)
        self.assertFalse(mock_logger.error.called)


@patch(MODULE_PATH + ".EveKillmail.objects.delete_stale")
class TestDeleteStaleKillmails(TestTrackerBase):
    def test_normal(self, mock_delete_stale):
        mock_delete_stale.return_value = (1, {"killtracker.EveKillmail": 1})
        delete_stale_killmails()
        self.assertTrue(mock_delete_stale.called)
